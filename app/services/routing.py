"""
Servicio de enrutamiento y simulación de GPS.

Provee:
- Cálculo de rutas reales usando OSRM (Docker)
- Generador de rutas fake con variaciones realistas (fallback o por elección)
- Interpolación de puntos a lo largo de una ruta
"""

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Literal

import httpx

OSRM_URL = "http://osrm:5000"


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
    Llama a OSRM para calcular ruta driving entre origen y destino.
    coords retorna lista de (lng, lat) en el orden de la ruta.
    Retorna None si OSRM no está disponible.
    """
    url = (
        f"{OSRM_URL}/route/v1/driving/"
        f"{origen[0]},{origen[1]};{destino[0]},{destino[1]}"
        f"?overview=full&geometries=geojson&steps=false"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != "Ok" or not data.get("routes"):
            return None

        route = data["routes"][0]
        coords_raw = route["geometry"]["coordinates"]
        coords = [(float(c[0]), float(c[1])) for c in coords_raw]

        return RouteResult(
            coords=coords,
            distancia_km=route["distance"] / 1000.0,
            duracion_seg=route["duration"],
        )
    except Exception:
        return None


def generar_ruta_fake(
    origen: tuple[float, float],
    destino: tuple[float, float],
    num_puntos: int = 50,
) -> RouteResult:
    """
    Genera una ruta fake 'realista' con variaciones simulando calles y dobleces.
    Usa Bezier simple con puntos de control aleatorios para simular el recorrido.
    No requiere servicio externo.
    """
    lon1, lat1 = origen
    lon2, lat2 = destino

    mid_lon = (lon1 + lon2) / 2
    mid_lat = (lat1 + lat2) / 2

    dx = lon2 - lon1
    dy = lat2 - lat1
    dist = math.sqrt(dx * dx + dy * dy)
    perp_x = -dy / dist if dist > 0 else 0
    perp_y = dx / dist if dist > 0 else 0

    max_deviation = dist * 0.15
    cp1_lon = mid_lon + perp_x * random.uniform(-max_deviation, max_deviation)
    cp1_lat = mid_lat + perp_y * random.uniform(-max_deviation, max_deviation)

    cp2_lon = mid_lon + perp_x * random.uniform(-max_deviation, max_deviation)
    cp2_lat = mid_lat + perp_y * random.uniform(-max_deviation, max_deviation)

    def cubic_bezier(t: float) -> tuple[float, float]:
        mt = 1 - t
        x = mt * mt * mt * lon1 + 3 * mt * mt * t * cp1_lon + 3 * mt * t * t * cp2_lon + t * t * t * lon2
        y = mt * mt * mt * lat1 + 3 * mt * mt * t * cp1_lat + 3 * mt * t * t * cp2_lat + t * t * t * lat2
        return x, y

    coords: list[tuple[float, float]] = []
    for i in range(num_puntos + 1):
        t = i / num_puntos
        coords.append(cubic_bezier(t))

    haversine_dist = _haversine_km(origen, destino)
    fake_dist = haversine_dist * random.uniform(1.3, 1.6)
    fake_duracion = (fake_dist / 40.0) * 3600

    return RouteResult(
        coords=coords,
        distancia_km=fake_dist,
        duracion_seg=fake_duracion,
    )


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
) -> RouteResult:
    """
    Obtiene ruta: intenta OSRM primero, si falla o usar_osrm=False
    genera ruta fake con variaciones realistas.
    """
    if usar_osrm:
        ruta = await calcular_ruta_osrm(origen, destino)
        if ruta:
            return ruta

    return generar_ruta_fake(origen, destino)


async def generar_puntos_interpolados(
    coords: list[tuple[float, float]],
    velocidad_kmh: float,
    intervalo_seg: float,
    distancia_total_km: float,
) -> list[tuple[float, float, float]]:
    """
    Genera puntos (lon, lat, tiempo_desde_inicio_seg) interpolados
    a lo largo de la ruta para una velocidad e intervalo dados.
    """
    if not coords or velocidad_kmh <= 0 or intervalo_seg <= 0:
        return []

    puntos: list[tuple[float, float, float]] = []
    tiempo_acum = 0.0
    distancia_acum = 0.0

    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]
        seg_dist = calcular_distancia_puntos(p1, p2)

        if seg_dist < 0.0001:
            continue

        tiempo_seg = (seg_dist / velocidad_kmh) * 3600
        num_pasos = max(1, int(round(seg_dist / (velocidad_kmh * intervalo_seg / 3600))))

        for j in range(num_pasos):
            t = j / num_pasos if num_pasos > 1 else 0.0
            lon = p1[0] * (1 - t) + p2[0] * t
            lat = p1[1] * (1 - t) + p2[1] * t
            puntos.append((lon, lat, tiempo_acum + t * tiempo_seg))
            distancia_acum += seg_dist * t

        tiempo_acum += tiempo_seg
        distancia_acum += seg_dist
        puntos.append((p2[0], p2[1], tiempo_acum))

    return puntos


def es_geocerca_cercana(
    posicion: tuple[float, float],
    destino: tuple[float, float],
    radio_m: float = 50.0,
) -> bool:
    """Verifica si posicion está dentro del radio (en metros) del destino."""
    dist_m = _haversine_km(posicion, destino) * 1000.0
    return dist_m <= radio_m