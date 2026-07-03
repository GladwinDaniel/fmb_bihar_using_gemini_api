import requests
from shapely.geometry import shape, LineString, Polygon
import urllib3
import math

# Disable warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Endpoints
SERVICES = {
    "NHRoads": "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/NHRoads/MapServer/0/query",
    "SHRoads": "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/SHRoads/MapServer/0/query",
    "MDR": "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/MDR/MapServer/0/query",
    "ROAD_BIHAR": "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/ROAD_BIHAR/MapServer/0/query",
    "Village_Road": "https://gisserver.bihar.gov.in/arcgis/rest/services/RWD/Village_Road/MapServer/0/query",
    "Rivers": "https://gisserver.bihar.gov.in/arcgis/rest/services/WATER_IRRIGATION/Rivers/MapServer/0/query",
    "Streams": "https://gisserver.bihar.gov.in/arcgis/rest/services/WATER_IRRIGATION/IrrigationStreams/MapServer/0/query"
}

PRIORITY = {
    "NHRoads": 1,
    "SHRoads": 2,
    "MDR": 3,
    "ROAD_BIHAR": 4,
    "Village_Road": 5
}

def query_service(bbox_str, url):
    params = {
        "geometry": bbox_str,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "f": "json",
        "outFields": "*",
        "returnGeometry": "true"
    }
    try:
        r = requests.get(url, params=params, verify=False, timeout=8)
        if r.status_code == 200:
            return r.json().get("features", [])
    except Exception as e:
        print(f"Error querying {url}: {e}")
    return []

def get_nearby_vector_features(parcel_polygon_gps):
    """
    Queries roads and rivers/streams within a buffered bbox of the parcel.
    Returns (roads_list, rivers_list) where each element is a dict:
    {
        'geometry': Shapely LineString,
        'name': str,
        'category': str, # NHRoads, SHRoads, etc.
        'priority': int,
        'distance_m': float # distance from parcel boundary (approx)
    }
    """
    bounds = parcel_polygon_gps.bounds # (minx, miny, maxx, maxy)
    # 50 meters buffer in degrees (approx 0.00045 degrees)
    buf = 0.0005
    bbox_str = f"{bounds[0]-buf},{bounds[1]-buf},{bounds[2]+buf},{bounds[3]+buf}"
    
    roads = []
    rivers = []
    
    # 1. Fetch Roads
    for cat in ["NHRoads", "SHRoads", "MDR", "ROAD_BIHAR", "Village_Road"]:
        features = query_service(bbox_str, SERVICES[cat])
        for f in features:
            geom_json = f.get("geometry")
            if not geom_json or "paths" not in geom_json:
                continue
            for path in geom_json["paths"]:
                try:
                    line = LineString(path)
                    # Calculate approximate distance in meters (1 deg lat ~ 111,000 meters)
                    dist_deg = parcel_polygon_gps.distance(line)
                    dist_m = dist_deg * 111000.0
                    
                    # Extract attributes
                    attrs = f.get("properties", f.get("attributes", {}))
                    name = attrs.get("Road_Name") or attrs.get("ROADNAME") or attrs.get("Rd_Name") or f"Road ({cat})"
                    
                    roads.append({
                        "geometry": line,
                        "name": name,
                        "category": cat,
                        "priority": PRIORITY[cat],
                        "distance_m": dist_m,
                        "attributes": attrs
                    })
                except Exception as e:
                    print(f"Error building road shape: {e}")
                    
    # 2. Fetch Rivers/Streams
    for cat in ["Rivers", "Streams"]:
        features = query_service(bbox_str, SERVICES[cat])
        for f in features:
            geom_json = f.get("geometry")
            if not geom_json:
                continue
            
            paths_or_rings = geom_json.get("paths") or geom_json.get("rings")
            if not paths_or_rings:
                continue
                
            for path in paths_or_rings:
                try:
                    line = LineString(path)
                    dist_deg = parcel_polygon_gps.distance(line)
                    dist_m = dist_deg * 111000.0
                    attrs = f.get("properties", f.get("attributes", {}))
                    name = attrs.get("RIVER_NAME") or attrs.get("NAME") or attrs.get("StreamName") or f"Waterbody ({cat})"
                    
                    rivers.append({
                        "geometry": line,
                        "name": name,
                        "category": cat,
                        "distance_m": dist_m,
                        "attributes": attrs
                    })
                except Exception as e:
                    print(f"Error building river shape: {e}")
                    
    # Sort roads: prioritize touching (distance ~ 0) first, then road priority, then distance
    roads.sort(key=lambda r: (r["distance_m"] > 5.0, r["priority"], r["distance_m"]))
    
    # Sort rivers by distance
    rivers.sort(key=lambda w: w["distance_m"])
    
    return roads, rivers
