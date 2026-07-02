import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def query_bihar_gis_features(min_lon, min_lat, max_lon, max_lat):
    """
    Queries Bihar GIS MapServer REST endpoints for roads and rivers within the bounding box.
    Returns a dictionary with 'roads' and 'rivers', each containing a list of polylines (list of GPS coordinates).
    """
    endpoints = {
        "roads": [
            "https://gisserver.bihar.gov.in/arcgis/rest/services/RWD/Village_Road/MapServer/0/query",
            "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/NHRoads/MapServer/0/query",
            "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/SHRoads/MapServer/0/query",
            "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/MDR/MapServer/0/query"
        ],
        "rivers": [
            "https://gisserver.bihar.gov.in/arcgis/rest/services/WATER_IRRIGATION/Rivers/MapServer/0/query",
            "https://gisserver.bihar.gov.in/arcgis/rest/services/WATER_IRRIGATION/IrrigationStreams/MapServer/0/query"
        ]
    }
    
    geom_env = {
        "xmin": min_lon,
        "ymin": min_lat,
        "xmax": max_lon,
        "ymax": max_lat,
        "spatialReference": {"wkid": 4326}
    }
    
    params = {
        "f": "json",
        "geometryType": "esriGeometryEnvelope",
        "geometry": json.dumps(geom_env),
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true",
        "outFields": "*"
    }
    
    results = {"roads": [], "rivers": []}
    
    for feature_type, urls in endpoints.items():
        for url in urls:
            try:
                resp = requests.get(url, params=params, verify=False, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for feature in data.get("features", []):
                        if "geometry" in feature:
                            if "paths" in feature["geometry"]:
                                for path in feature["geometry"]["paths"]:
                                    results[feature_type].append({
                                        "path": path,
                                        "attributes": feature.get("attributes", {})
                                    })
                            elif "rings" in feature["geometry"]:
                                for ring in feature["geometry"]["rings"]:
                                    results[feature_type].append({
                                        "path": ring,
                                        "attributes": feature.get("attributes", {})
                                    })
            except Exception as e:
                print(f"Error querying {url}: {e}")
                
    return results
