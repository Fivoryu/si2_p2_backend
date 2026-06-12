"""
Servicio de enrutamiento y simulación de GPS.

Provee:
- Cálculo de rutas reales usando OSRM (Docker)
- Generador de rutas fake con variaciones realistas (fallback o por elección)
- Interpolación de puntos a lo largo de una ruta
"""

import asyncio
import logging
import math
from dataclasses import dataclass

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

OSRM_FALLBACK_URLS = (
    settings.osrm_url,
    settings.osrm_public_url,
)


@dataclass
class RouteResult:
    coords: list[tuple[float, float]]  # [(lng, lat), ...]
    distancia_km: float
    duracion_seg: float
    geometry: str | None = None  # polyline encoded opcional


async def calcular_ruta_osrm(
    origen: tuple[float, float],
    destino: tuple[float, float],
) -> RouteResult | None:
    """
    Llama a OSRM (local Docker o servidor público) para ruta driving.
    Retorna None si ningún servidor responde.
    """
    path = (
        f"/route/v1/driving/"
        f"{origen[0]},{origen[1]};{destino[0]},{destino[1]}"
        f"?overview=full&geometries=geojson&steps=false"
    )

    for base in OSRM_FALLBACK_URLS:
        if not base:
            continue
        url = f"{base.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != "Ok" or not data.get("routes"):
                continue

            route = data["routes"][0]
            coords_raw = route["geometry"]["coordinates"]
            coords = [(float(c[0]), float(c[1])) for c in coords_raw]
            if len(coords) < 2:
                continue

            logger.info("Ruta OSRM obtenida desde %s (%d puntos)", base, len(coords))
            return RouteResult(
                coords=coords,
                distancia_km=route["distance"] / 1000.0,
                duracion_seg=route["duration"],
            )
        except Exception as exc:
            logger.warning("OSRM no disponible en %s: %s", base, exc)
            continue

    return None


def generar_ruta_fake(
    origen: tuple[float, float],
    destino: tuple[float, float],
    num_puntos: int = 50,
) -> RouteResult:
    """Fallback: ruta tipo calles con tramos ortogonales cada ~30 m."""
    return generar_ruta_calles(origen, destino, paso_km=0.03)


def generar_ruta_calles(
    origen: tuple[float, float],
    destino: tuple[float, float],
    paso_km: float = 0.03,
) -> RouteResult:
    """
    Simula recorrido por calles con tramos en L (avenida + calle).
    Inserta puntos cada paso_km para movimiento fluido.
    """
    lon1, lat1 = origen
    lon2, lat2 = destino

    dx = abs(lon2 - lon1)
    dy = abs(lat2 - lat1)
    # Primero el eje con mayor separación (como seguir avenida principal)
    if dx >= dy:
        waypoints = [origen, (lon2, lat1), destino]
    else:
        waypoints = [origen, (lon1, lat2), destino]

    coords: list[tuple[float, float]] = []
    for i, (a, b) in enumerate(zip(waypoints, waypoints[1:])):
        segment = _interp_segmento(a, b, paso_km)
        if i > 0 and segment:
            segment = segment[1:]
        coords.extend(segment)

    if not coords:
        coords = [origen, destino]

    total_km = sum(
        calcular_distancia_puntos(coords[i], coords[i + 1])
        for i in range(len(coords) - 1)
    )
    duracion = (total_km / 40.0) * 3600 if total_km > 0 else 0

    return RouteResult(
        coords=coords,
        distancia_km=total_km,
        duracion_seg=duracion,
    )


def _interp_segmento(
    p1: tuple[float, float],
    p2: tuple[float, float],
    paso_km: float,
) -> list[tuple[float, float]]:
    dist = calcular_distancia_puntos(p1, p2)
    if dist < 0.0001:
        return [p1]
    steps = max(2, int(dist / paso_km) + 1)
    pts: list[tuple[float, float]] = []
    for i in range(steps):
        t = i / (steps - 1)
        lon = p1[0] * (1 - t) + p2[0] * t
        lat = p1[1] * (1 - t) + p2[1] * t
        pts.append((lon, lat))
    return pts


def interpolar_punto(
    coords: list[tuple[float, float]],
    fraccion: float,
) -> tuple[float, float]:
    """
    Retorna el punto en la ruta correspondiente a la fracción 0.0-1.0.
    Interpola linealmente entre puntos consecutivos de la ruta.
    """
    if not coords:
        return (0.0, 0.0)
    if len(coords) == 1:
        return coords[0]
    fraccion = max(0.0, min(1.0, fraccion))

    total_segments = len(coords) - 1
    target_idx_float = fraccion * total_segments
    idx = int(target_idx_float)
    t = target_idx_float - idx

    if idx >= total_segments:
        return coords[-1]

    lon = coords[idx][0] * (1 - t) + coords[idx + 1][0] * t
    lat = coords[idx][1] * (1 - t) + coords[idx + 1][1] * t
    return (lon, lat)


def calcular_distancia_puntos(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Distancia en km entre dos puntos (lng, lat)."""
    return _haversine_km(p1, p2)


def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    R = 6371.0
    lon1, lat1 = math.radians(p1[0]), math.radians(p1[1])
    lon2, lat2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


async def obtener_ruta(
    origen: tuple[float, float],
    destino: tuple[float, float],
    usar_osrm: bool = True,
) -> tuple[RouteResult, str]:
    """
    Obtiene ruta. Retorna (RouteResult, motor) donde motor es 'osrm' o 'calles'.
    """
    if usar_osrm:
        ruta = await calcular_ruta_osrm(origen, destino)
        if ruta:
            return ruta, "osrm"

    logger.info("Usando ruta simulada por calles (fallback)")
    return generar_ruta_fake(origen, destino), "calles"


async def generar_puntos_interpolados(
    coords: list[tuple[float, float]],
    velocidad_kmh: float,
    intervalo_seg: float,
    distancia_total_km: float | None = None,
) -> list[tuple[float, float, float]]:
    """
    Genera puntos (lon, lat, tiempo_desde_inicio_seg) a intervalos fijos
    recorriendo toda la polilínea de la ruta.
    """
    if not coords or velocidad_kmh <= 0 or intervalo_seg <= 0:
        return []

    cum_dist = [0.0]
    for i in range(1, len(coords)):
        cum_dist.append(
            cum_dist[-1] + calcular_distancia_puntos(coords[i - 1], coords[i])
        )

    total_km = distancia_total_km if distancia_total_km is not None else cum_dist[-1]
    if total_km <= 0:
        return [(coords[0][0], coords[0][1], 0.0)]

    speed_km_s = velocidad_kmh / 3600.0
    step_km = speed_km_s * intervalo_seg

    puntos: list[tuple[float, float, float]] = []
    d = 0.0
    t = 0.0
    while d <= total_km + 1e-9:
        lon, lat = _interpolar_por_distancia(coords, cum_dist, d)
        puntos.append((lon, lat, t))
        if d >= total_km:
            break
        d = min(d + step_km, total_km)
        t += intervalo_seg

    last = coords[-1]
    if not puntos or puntos[-1][:2] != last:
        puntos.append((last[0], last[1], t))

    return puntos


def _interpolar_por_distancia(
    coords: list[tuple[float, float]],
    cum_dist: list[float],
    km: float,
) -> tuple[float, float]:
    if km <= 0:
        return coords[0]
    if km >= cum_dist[-1]:
        return coords[-1]

    lo, hi = 0, len(cum_dist) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum_dist[mid] < km:
            lo = mid + 1
        else:
            hi = mid

    idx = max(1, lo)
    seg_len = cum_dist[idx] - cum_dist[idx - 1]
    if seg_len <= 0:
        return coords[idx]

    t = (km - cum_dist[idx - 1]) / seg_len
    lon = coords[idx - 1][0] * (1 - t) + coords[idx][0] * t
    lat = coords[idx - 1][1] * (1 - t) + coords[idx][1] * t
    return (lon, lat)


def es_geocerca_cercana(
    posicion: tuple[float, float],
    destino: tuple[float, float],
    radio_m: float = 50.0,
) -> bool:
    """Verifica si posicion está dentro del radio (en metros) del destino."""
    dist_m = _haversine_km(posicion, destino) * 1000.0
    return dist_m <= radio_m