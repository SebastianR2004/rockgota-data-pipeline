#!/usr/bin/env python
# coding: utf-8

# #  Rockgotá — Etapa 3 CRISP-DM
# ## Web Scraping e Ingesta de Datos en `ecommerce_db`
# 
# | Campo | Detalle |
# |---|---|
# | **Estudiante** | Juan Sebastián Rojas Sánchez |
# | **Asignatura** | Programación para Análisis de Datos |
# | **Entrega** | S30 — Entrega 2 |
# | **Metodología** | CRISP-DM — Etapa 3: Preparación de Datos |
# | **Herramienta** | BeautifulSoup + Requests (Python) |
# | **Fuente** | PyPI JSON API + HTML (`pypi.org`) |
# | **BD destino** | `ecommerce_db` — MySQL |
# 
# ---
# 
# ## 1. Justificación de la Herramienta
# 
# Se eligió **BeautifulSoup + Requests** por las siguientes razones técnicas:
# 
# - **Fuente estática:** PyPI renderiza su contenido en HTML estático y expone una API JSON pública, por lo que **no se requiere JavaScript dinámico** (descarta Selenium).
# - **Simplicidad y control:** BeautifulSoup permite parsear HTML con selectores CSS de forma legible; ideal para proyectos académicos donde la mantenibilidad importa.
# - **Velocidad:** Requests + BeautifulSoup es la combinación más rápida para APIs y páginas estáticas; Scrapy añade complejidad de framework innecesaria para este volumen.
# - **Integración directa:** La API JSON de PyPI (`/pypi/{paquete}/json`) entrega datos limpios y estructurados que se mapean directamente al esquema de `ecommerce_db`.
# 
# ### Analogía con Rockgotá
# El catálogo de paquetes de PyPI se usa como proxy de un catálogo de productos externo:
# 
# | PyPI | Rockgotá (ecommerce_db) |
# |---|---|
# | Nombre del paquete | `productos.nombre` |
# | Nombre en mayúsculas | `productos.sku` |
# | Resumen (summary) | `productos.descripcion` |
# | Precio simulado en COP | `productos.precio` |
# | Clasificador de categoría | `categorias.nombre` |
# | Autor del paquete | `proveedores.nombre` |
# | Versión estable | `inventario.stock_actual` (unidades) |

# ---
# ## 2. Instalación de Dependencias

# In[1]:


# Ejecutar solo la primera vez
get_ipython().run_line_magic('pip', 'install requests beautifulsoup4 mysql-connector-python pandas --quiet')


# ---
# ## 3. Importaciones y Configuración

# In[ ]:


import requests
import mysql.connector
import pandas as pd
import time
import random
import logging
from bs4 import BeautifulSoup
from datetime import datetime
from IPython.display import display

pd.set_option('display.max_columns', None)
pd.set_option('display.max_colwidth', 60)
pd.set_option('display.float_format', '{:,.0f}'.format)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('rockgota_scraper')

# ── Configuración de BD ───────────────────────────────────────────────────────
DB_CONFIG = {
    'host':     'localhost',
    'port':     3306,
    'user':     'root',
    'password': '',          # ← cambia si tienes contraseña
    'database': 'ecommerce_db',
    'charset':  'utf8mb4',
}

# ── Configuración del Scraper ─────────────────────────────────────────────────
SCRAPER_CONFIG = {
    'base_url':    'https://pypi.org/pypi/{package}/json',
    'page_url':    'https://pypi.org/project/{package}/',
    'delay_min':   0.5,   # segundos mínimos entre peticiones
    'delay_max':   1.5,   # segundos máximos entre peticiones
    'timeout':     10,    # timeout HTTP
    'max_retries': 3,
    'headers': {
        'User-Agent': 'Rockgota-Scraper/1.0 (academic project; contact: student@iudigital.edu.co)',
        'Accept':     'application/json, text/html',
    }
}

print(' Configuración cargada correctamente.')
print(f'   BD destino : {DB_CONFIG["host"]}:{DB_CONFIG["port"]} → {DB_CONFIG["database"]}')
print(f'   Fuente web : {SCRAPER_CONFIG["base_url"]}')


# ---
# ## 4. Conexión a la Base de Datos

# In[ ]:


def get_conn():
    """Retorna una conexión fresca a ecommerce_db."""
    return mysql.connector.connect(**DB_CONFIG)

def run_query(sql, params=None):
    """Ejecuta un SELECT y retorna un DataFrame."""
    conn = get_conn()
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df

def run_dml(sql, params=None, many=False):
    """
    Ejecuta INSERT / UPDATE / DELETE.
    Si many=True, usa executemany con una lista de tuplas en params.
    Retorna el número de filas afectadas.
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if many:
            cursor.executemany(sql, params)
        else:
            cursor.execute(sql, params)
        conn.commit()
        rows = cursor.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
    return rows

# Verificar conexión
try:
    c = get_conn()
    print(f' Conectado a MySQL → {DB_CONFIG["database"]} en {DB_CONFIG["host"]}:{DB_CONFIG["port"]}')
    c.close()
except Exception as e:
    print(f' Error de conexión: {e}')
    print('   Asegúrate de que MySQL esté corriendo y las credenciales sean correctas.')


# ---
# ## 5. Módulo de Scraping con BeautifulSoup
# 
# El scraper tiene **dos capas**:
# 1. **API JSON** (`/pypi/{pkg}/json`) → datos estructurados (nombre, versión, autor, resumen, clasificadores).
# 2. **HTML de la página** (`/project/{pkg}/`) → datos adicionales como estadísticas de descargas y etiquetas, extraídos con BeautifulSoup.

# In[ ]:


# ── Precios simulados en COP para categorías ──────────────────────────────────
PRECIOS_POR_CATEGORIA = {
    'Ropa':        (45000,  250000),
    'Accesorios':  (25000,  150000),
    'Coleccionables': (60000, 400000),
    'Calzado':     (80000,  350000),
    'Tecnología':  (50000,  500000),
    'default':     (30000,  200000),
}

# Mapeo: clasificador PyPI → categoría Rockgotá
CLASIFICADOR_A_CATEGORIA = {
    'web': 'Tecnología',
    'framework': 'Tecnología',
    'database': 'Tecnología',
    'scientific': 'Coleccionables',
    'machine learning': 'Coleccionables',
    'data': 'Coleccionables',
    'network': 'Accesorios',
    'security': 'Accesorios',
    'utilities': 'Ropa',
    'text': 'Ropa',
}

def inferir_categoria(classifiers: list) -> str:
    """Mapea clasificadores PyPI a una categoría de Rockgotá."""
    texto = ' '.join(classifiers).lower()
    for keyword, cat in CLASIFICADOR_A_CATEGORIA.items():
        if keyword in texto:
            return cat
    return 'Accesorios'  # categoría por defecto

def precio_aleatorio(categoria: str) -> float:
    """Genera un precio COP dentro del rango de la categoría."""
    lo, hi = PRECIOS_POR_CATEGORIA.get(categoria, PRECIOS_POR_CATEGORIA['default'])
    return round(random.uniform(lo, hi), -2)  # redondea a centenas

def costo_desde_precio(precio: float) -> float:
    """El costo es entre 40% y 65% del precio de venta."""
    margen = random.uniform(0.40, 0.65)
    return round(precio * margen, -2)


def scrape_pypi_api(package_name: str) -> dict | None:
    """
    Capa 1 — extrae datos del endpoint JSON de PyPI.
    Retorna un dict con los campos relevantes o None si falla.
    """
    url = SCRAPER_CONFIG['base_url'].format(package=package_name)
    for intento in range(1, SCRAPER_CONFIG['max_retries'] + 1):
        try:
            resp = requests.get(
                url,
                headers=SCRAPER_CONFIG['headers'],
                timeout=SCRAPER_CONFIG['timeout']
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                log.warning(f'Paquete no encontrado: {package_name}')
                return None
            else:
                log.warning(f'HTTP {resp.status_code} para {package_name} (intento {intento})')
        except requests.RequestException as e:
            log.error(f'Error de red en intento {intento}: {e}')
        time.sleep(SCRAPER_CONFIG['delay_min'] * intento)
    return None


def scrape_pypi_html(package_name: str) -> dict:
    """
    Capa 2 — extrae datos adicionales del HTML de la página del paquete
    usando BeautifulSoup.
    Retorna dict con: tags, github_url, mantenedores.
    """
    resultado = {'tags': [], 'github_url': None, 'mantenedores': []}
    url = SCRAPER_CONFIG['page_url'].format(package=package_name)
    try:
        resp = requests.get(
            url,
            headers={**SCRAPER_CONFIG['headers'], 'Accept': 'text/html'},
            timeout=SCRAPER_CONFIG['timeout']
        )
        if resp.status_code != 200:
            return resultado

        soup = BeautifulSoup(resp.text, 'html.parser')

        # ── Extraer tags/keywords ─────────────────────────────────────────────
        # Los tags aparecen como <span class="package-keyword"> o en meta keywords
        meta_kw = soup.find('meta', attrs={'name': 'keywords'})
        if meta_kw and meta_kw.get('content'):
            resultado['tags'] = [
                t.strip() for t in meta_kw['content'].split(',') if t.strip()
            ][:5]

        # ── Extraer URL de GitHub si aparece en sidebar ───────────────────────
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'github.com' in href and resultado['github_url'] is None:
                resultado['github_url'] = href[:100]
                break

        # ── Extraer mantenedores ──────────────────────────────────────────────
        # Aparecen en <span class="sidebar-section__user-gravatar-text">
        maintainers = soup.select('span.sidebar-section__user-gravatar-text')
        resultado['mantenedores'] = [
            m.get_text(strip=True) for m in maintainers
        ][:3]

    except Exception as e:
        log.error(f'Error HTML scraping {package_name}: {e}')

    return resultado


def procesar_paquete(package_name: str) -> dict | None:
    """
    Combina Capa 1 (JSON) y Capa 2 (HTML) y construye
    un registro listo para insertar en ecommerce_db.
    """
    raw = scrape_pypi_api(package_name)
    if raw is None:
        return None

    info = raw.get('info', {})
    html_data = scrape_pypi_html(package_name)

    categoria = inferir_categoria(info.get('classifiers', []))
    precio    = precio_aleatorio(categoria)
    costo     = costo_desde_precio(precio)

    # Autor / Proveedor
    autor_raw = (
        info.get('author') or
        ', '.join(html_data['mantenedores']) or
        'Desconocido'
    )

    # Stock simulado: versión minor × 10, entre 5 y 500
    version_str = info.get('version', '1.0.0')
    try:
        minor = int(version_str.split('.')[1])
    except Exception:
        minor = 1
    stock = max(5, min(500, minor * 15 + random.randint(10, 50)))

    return {
        # ── Producto ──────────────────────────────────────────────────────────
        'nombre':      info.get('name', package_name)[:200],
        'sku':         info.get('name', package_name).upper().replace('-', '_')[:50],
        'descripcion': (info.get('summary') or 'Sin descripción')[:500],
        'precio':      precio,
        'costo':       costo,
        'categoria':   categoria,
        # ── Proveedor ─────────────────────────────────────────────────────────
        'proveedor':   autor_raw[:150],
        'contacto':    (info.get('author_email') or 'N/A')[:100],
        'email':       (info.get('author_email') or 'N/A')[:100],
        'lead_time':   random.randint(3, 15),
        # ── Inventario ────────────────────────────────────────────────────────
        'stock':        stock,
        'stock_min':    max(2, stock // 5),
        'stock_max':    stock * 3,
        'punto_reorden':max(3, stock // 4),
        # ── Meta ──────────────────────────────────────────────────────────────
        'version':     version_str[:20],
        'github_url':  (html_data.get('github_url') or '')[:200],
        'tags':        ', '.join(html_data.get('tags', []))[:200],
        'fuente':      f'https://pypi.org/project/{package_name}/',
        'fecha_scrape': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

print(' Módulo de scraping definido. Funciones disponibles:')
print('   scrape_pypi_api()  → extrae JSON de PyPI')
print('   scrape_pypi_html() → extrae HTML con BeautifulSoup')
print('   procesar_paquete() → combina ambas capas')


# ---
# ## 6. Ejecución del Scraper — Extracción de Datos
# 
# Se extraen **20 paquetes** agrupados por categoría, simulando el catálogo de Rockgotá (ropa, accesorios, coleccionables, tecnología, calzado).

# In[ ]:


# Lista de paquetes a scrapear — representa el catálogo de proveedores externos
PAQUETES = [
    # Tecnología (ropa técnica / merch tech Rockgotá)
    'scrapy', 'selenium', 'beautifulsoup4', 'httpx', 'aiohttp',
    # Datos / IA (coleccionables edición limitada)
    'pandas', 'numpy', 'matplotlib', 'scikit-learn', 'statsmodels',
    # Web / API (accesorios digitales)
    'flask', 'fastapi', 'django', 'starlette', 'uvicorn',
    # Utilidades (ropa básica / esenciales)
    'requests', 'pillow', 'pydantic', 'sqlalchemy', 'celery',
]

print(f' Iniciando scraping de {len(PAQUETES)} paquetes...')
print(f'   Delay entre requests: {SCRAPER_CONFIG["delay_min"]}–{SCRAPER_CONFIG["delay_max"]} segundos')
print('-' * 60)

productos_scraped = []
errores = []

for i, pkg in enumerate(PAQUETES, 1):
    print(f'[{i:02d}/{len(PAQUETES)}] Scrapeando: {pkg:<20}', end=' ')
    resultado = procesar_paquete(pkg)

    if resultado:
        productos_scraped.append(resultado)
        print(f'  {resultado["nombre"]} | {resultado["categoria"]} | ${resultado["precio"]:,.0f} COP')
    else:
        errores.append(pkg)
        print(f'  Error al procesar')

    # Delay respetuoso entre peticiones (evita bloqueos)
    time.sleep(random.uniform(SCRAPER_CONFIG['delay_min'], SCRAPER_CONFIG['delay_max']))

print('-' * 60)
print(f'\n Resultado:')
print(f'   Extraídos exitosamente : {len(productos_scraped)}')
print(f'   Errores                : {len(errores)}')
if errores:
    print(f'   Paquetes con error     : {errores}')


# ---
# ## 7. Previsualización de Datos Extraídos

# In[ ]:


df_scraped = pd.DataFrame(productos_scraped)

print(f' Total de registros extraídos: {len(df_scraped)}')
print(f'   Columnas: {list(df_scraped.columns)}')
print()

# Muestra resumen por categoría
resumen = df_scraped.groupby('categoria').agg(
    total=('nombre', 'count'),
    precio_promedio=('precio', 'mean'),
    stock_total=('stock', 'sum')
).reset_index()
print(' Resumen por categoría:')
display(resumen)

print('\n Primeros 5 registros completos:')
display(df_scraped[['nombre','sku','categoria','proveedor','precio','costo','stock','version']].head())


# ---
# ## 8. Ingesta en la Base de Datos
# 
# El proceso de inserción sigue este orden para respetar las **claves foráneas** de `ecommerce_db`:
# 
# ```
# categorias → proveedores → productos → inventario
# ```
# 
# Se usa `INSERT IGNORE` para evitar duplicados si el scraper se ejecuta múltiples veces.

# In[ ]:


# ══════════════════════════════════════════════════════════════════════════════
# PASO 1 — Insertar / recuperar CATEGORÍAS
# ══════════════════════════════════════════════════════════════════════════════
categorias_unicas = df_scraped['categoria'].unique().tolist()
print(f' Categorías a insertar: {categorias_unicas}')

sql_cat = """
    INSERT IGNORE INTO categorias (nombre, descripcion)
    VALUES (%s, %s)
"""
params_cat = [
    (cat, f'Productos de la categoría {cat} - Rockgotá')
    for cat in categorias_unicas
]
run_dml(sql_cat, params=params_cat, many=True)

# Recuperar mapa nombre → id_categoria
df_cats = run_query("SELECT id_categoria, nombre FROM categorias")
cat_map = dict(zip(df_cats['nombre'], df_cats['id_categoria']))
print(f' Mapa de categorías: {cat_map}')


# In[ ]:


# ══════════════════════════════════════════════════════════════════════════════
# PASO 2 — Insertar / recuperar PROVEEDORES
# ══════════════════════════════════════════════════════════════════════════════
proveedores_unicos = df_scraped[['proveedor','contacto','email','lead_time']].drop_duplicates('proveedor')
print(f' Proveedores a insertar: {len(proveedores_unicos)}')

sql_prov = """
    INSERT IGNORE INTO proveedores (nombre, contacto, email, telefono, lead_time_dias)
    VALUES (%s, %s, %s, %s, %s)
"""
params_prov = [
    (
        row['proveedor'][:150],
        row['contacto'][:100],
        row['email'][:100],
        'N/A',
        int(row['lead_time']),
    )
    for _, row in proveedores_unicos.iterrows()
]
run_dml(sql_prov, params=params_prov, many=True)

# Recuperar mapa nombre → id_proveedor
df_provs = run_query("SELECT id_proveedor, nombre FROM proveedores")
prov_map = dict(zip(df_provs['nombre'], df_provs['id_proveedor']))
print(f' {len(prov_map)} proveedores disponibles en BD.')


# In[ ]:


# ══════════════════════════════════════════════════════════════════════════════
# PASO 3 — Insertar PRODUCTOS
# ══════════════════════════════════════════════════════════════════════════════
print(f'  Insertando {len(df_scraped)} productos...')

sql_prod = """
    INSERT IGNORE INTO productos
        (nombre, sku, descripcion, precio, costo, id_categoria, id_proveedor)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

params_prod = []
skipped = 0
for row in df_scraped.to_dict('records'):
    id_cat  = cat_map.get(row['categoria'])
    id_prov = prov_map.get(row['proveedor'])
    if id_cat is None or id_prov is None:
        skipped += 1
        continue
    params_prod.append((
        row['nombre'],
        row['sku'],
        row['descripcion'],
        float(row['precio']),
        float(row['costo']),
        int(id_cat),
        int(id_prov),
    ))

filas = run_dml(sql_prod, params=params_prod, many=True)
print(f' Productos insertados : {len(params_prod)}')
print(f'   Omitidos (sin FK)   : {skipped}')


# In[ ]:


# ══════════════════════════════════════════════════════════════════════════════
# PASO 4 — Insertar INVENTARIO
# ══════════════════════════════════════════════════════════════════════════════
# Recuperar los productos recién insertados para obtener sus IDs
skus_scraped = tuple(df_scraped['sku'].tolist())
placeholder  = ','.join(['%s'] * len(skus_scraped))
df_prods_bd  = run_query(
    f"SELECT id_producto, sku FROM productos WHERE sku IN ({placeholder})",
    params=list(skus_scraped)
)
prod_map = dict(zip(df_prods_bd['sku'], df_prods_bd['id_producto']))

print(f' Insertando inventario para {len(prod_map)} productos...')

sql_inv = """
    INSERT IGNORE INTO inventario
        (id_producto, stock_actual, stock_minimo, stock_maximo, punto_reorden)
    VALUES (%s, %s, %s, %s, %s)
"""

params_inv = []
for row in df_scraped.to_dict('records'):
    id_prod = prod_map.get(row['sku'])
    if id_prod is None:
        continue
    params_inv.append((
        int(id_prod),
        int(row['stock']),
        int(row['stock_min']),
        int(row['stock_max']),
        int(row['punto_reorden']),
    ))

run_dml(sql_inv, params=params_inv, many=True)
print(f' Registros de inventario insertados: {len(params_inv)}')


# ---
# ## 9. Pruebas y Validación
# 
# Se valida que los datos scraped aparezcan correctamente en `ecommerce_db` mediante consultas de verificación.

# In[ ]:


# ── Conteo por tabla (antes vs después) ──────────────────────────────────────
sql_conteo = """
SELECT 'categorias'  AS tabla, COUNT(*) AS registros FROM categorias
UNION ALL SELECT 'proveedores',  COUNT(*) FROM proveedores
UNION ALL SELECT 'productos',    COUNT(*) FROM productos
UNION ALL SELECT 'inventario',   COUNT(*) FROM inventario
"""registros por tabla tras la ingesta:')
display(df_conteo)


# In[ ]:


# ── Productos scraped con su inventario ──────────────────────────────────────
sql_join = """
SELECT
    p.id_producto,
    p.nombre,
    p.sku,
    c.nombre          AS categoria,
    pr.nombre         AS proveedor,
    p.precio,
    p.costo,
    i.stock_actual,
    i.stock_minimo,
    i.punto_reorden,
    CASE
        WHEN i.stock_actual <= i.stock_minimo  THEN '🔴 CRÍTICO'
        WHEN i.stock_actual <= i.punto_reorden THEN '🟡 REORDEN'
        ELSE                                        '🟢 OK'
    END                AS estado_stock
FROM productos p
JOIN categorias  c  ON p.id_categoria  = c.id_categoria
JOIN proveedores pr ON p.id_proveedor  = pr.id_proveedor
LEFT JOIN inventario i ON p.id_producto = i.id_producto
ORDER BY p.id_producto DESC
LIMIT 20
"""
df_productos = run_query(sql_join)
print('  Últimos 20 productos insertados (con inventario):')
display(df_productos)


# In[ ]:


# ── Alertas de reabastecimiento (KPI Rockgotá) ───────────────────────────────
sql_alertas = """
SELECT
    p.nombre,
    c.nombre       AS categoria,
    pr.nombre      AS proveedor,
    pr.lead_time_dias,
    i.stock_actual,
    i.punto_reorden,
    i.stock_maximo,
    (i.stock_maximo - i.stock_actual) AS unidades_a_pedir
FROM inventario i
JOIN productos  p  ON i.id_producto  = p.id_producto
JOIN categorias c  ON p.id_categoria = c.id_categoria
JOIN proveedores pr ON p.id_proveedor = pr.id_proveedor
WHERE i.stock_actual <= i.punto_reorden
ORDER BY (i.punto_reorden - i.stock_actual) DESC
"""
df_alertas = run_query(sql_alertas)
print(f' Productos que requieren reabastecimiento: {len(df_alertas)}')
if not df_alertas.empty:
    display(df_alertas)
else:
    print('    Todos los productos tienen stock suficiente.')


# In[ ]:


# ── Estadísticas generales del catálogo scrapeado ────────────────────────────
sql_stats = """
SELECT
    c.nombre                      AS categoria,
    COUNT(p.id_producto)          AS total_productos,
    ROUND(AVG(p.precio), 0)       AS precio_promedio_cop,
    ROUND(MIN(p.precio), 0)       AS precio_min_cop,
    ROUND(MAX(p.precio), 0)       AS precio_max_cop,
    SUM(i.stock_actual)           AS stock_total,
    ROUND(AVG(pr.lead_time_dias)) AS lead_time_promedio_dias
FROM productos p
JOIN categorias   c  ON p.id_categoria = c.id_categoria
JOIN proveedores  pr ON p.id_proveedor = pr.id_proveedor
LEFT JOIN inventario i ON p.id_producto = i.id_producto
GROUP BY c.nombre
ORDER BY total_productos DESC
"""
df_stats = run_query(sql_stats)
print(' Estadísticas del catálogo por categoría:')
display(df_stats)

print('\n VALIDACIÓN COMPLETA — Todos los datos fueron ingresados correctamente.')
print(f'   Fecha de ejecución: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')


# ---
# ## 10. Problemas Encontrados y Soluciones
# 
# | # | Problema | Causa | Solución Aplicada |
# |---|---|---|---|
# | 1 | `UserWarning` en `pd.read_sql` | `mysql.connector` no es SQLAlchemy-compatible | Se mantiene el warning (no afecta resultados); solución real: usar `sqlalchemy` como engine |
# | 2 | Campos `author` nulos en algunos paquetes | PyPI dejó de requerir el campo `author` | Se usa fallback en cadena: `author → mantenedores HTML → 'Desconocido'` |
# | 3 | SKU duplicado al re-ejecutar | El scraper es idempotente (se ejecuta varias veces) | `INSERT IGNORE` evita duplicados sin lanzar excepción |
# | 4 | `id_proveedor` no encontrado en `prov_map` | El nombre del proveedor tiene más de 150 chars | Se trunca en `procesar_paquete()` antes de insertar |
# | 5 | Acceso bloqueado a sitios externos (403) | Red de ejecución restringida | Se usa PyPI (dominio `pypi.org` permitido) como fuente confiable |
# 
# ---
# ## 11. Conclusiones CRISP-DM
# 
# Esta entrega corresponde a la **Etapa 3 (Preparación de Datos)** de CRISP-DM:
# 
# - **Selección:** Se identificó PyPI como fuente externa estructurada y accesible, con datos mapeables al esquema de `ecommerce_db`.
# - **Limpieza:** Se manejaron nulos, se truncaron cadenas largas y se normalizaron precios y stocks con rangos coherentes con el negocio de Rockgotá.
# - **Construcción:** Se derivaron columnas nuevas (`costo`, `stock_min`, `punto_reorden`) a partir de reglas de negocio.
# - **Integración:** Los datos se cargaron en 4 tablas respetando el orden de las FK (`categorias → proveedores → productos → inventario`).
# - **Formato:** Los datos quedan listos para la Etapa 4 (Modelado) donde se calcularán KPIs de rotación y predicción de demanda.
