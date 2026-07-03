// =============================================================================
// BHUOVERLAY  Bihar Cadastral Map & Satellite Dashboard
// Frontend Application Logic (app.js)
// =============================================================================
//
// ROLE: This file contains ALL client-side logic for the application.
//   It runs entirely in the browser and communicates with the Flask backend
//   (backend/app.py running on http://127.0.0.1:5001) via jQuery AJAX calls.
//
// STRUCTURE:
//   1.  Global State Variables
//   2.  $(document).ready() Entry Point
//   3.  Map Layer Initialization (initMap)
//   4.  Dropdown Initialization (initDropdowns)
//   5.  Dropdown Fetch Logic (fetchDropdown)
//   6.  Event Listeners Setup (setupEventListeners)
//       - Opacity slider, sidebar toggles, dropdown cascade
//       - Basemap switcher, layer toggles
//       - Map click-to-select, PNIU search
//       - Map image download, sheet clone (batch scraper)
//       - Kurra Division (subdivide), PDF report download
//   7.  loadVillageSheet()  Sheet selection and WMS overlay
//   8.  executePniuSearch()  Search by 14-digit PNIU code
//   9.  clearDetails()  Reset sidebar and map vector layers
//   10. selectPlotByNumber()  Fetch full parcel details by plot number
//   11. displayParcelDetails()  Render parcel data in sidebar + update map
//   12. redrawSelectedVector()  Draw polygon boundary on OpenLayers map
//   13. zoomToParcel()  Fit map view to parcel bounding box
//   14. Helper functions (resetDropdowns, getLevelLabel, showLoading, showToast)
//
// DEPENDENCIES (loaded via CDN in index.html):
//   OpenLayers v6 (ol)  GIS map rendering engine
//   jQuery ($)           DOM manipulation and AJAX
//
// CONNECTED TO (see CONNECTION_GUIDE.md for full API contract):
//   Backend: http://127.0.0.1:5001 (Flask, backend/app.py)
//
// HOW THE FRONTEND KNOWS THE BACKEND ADDRESS:
//   API_BASE_URL auto-detects development (localhost/file://) vs production.
//   In dev: all calls go to http://127.0.0.1:5001/...
//   In prod: calls use relative URLs (same-origin server serves both)
// =============================================================================

const isDev = window.location.protocol === 'file:' || 
              (window.location.port && !['5001', '80', '443'].includes(window.location.port));
const API_BASE_URL = isDev
    ? `${window.location.protocol === 'file:' ? 'http:' : window.location.protocol}//${window.location.hostname || '127.0.0.1'}:5001`
    : ''; // empty string in production/same-origin


// --- STEP 2: Global State Variables ---
// These variables hold application-wide state. All major functions read/write these.
// They act as the "single source of truth" for the current map and parcel state.
let map;              // The main OpenLayers Map instance
let plotLyr;          // WMS Cadastral Map layer (BhuNaksha VILLAGE_MAP)
let selPlotLyr;       // WMS Selected Plot Highlight layer (BhuNaksha PLOT_LIST)
let vectorSource;     // OpenLayers Vector Source for the selected parcel polygon
let vectorLayer;      // OpenLayers Vector Layer that renders vectorSource
let osmVectorLayer;   // Vector layer for manually placed trees/wells
let kurraVectorLayer; // Vector layer for Kurra sub-division polygons
let osmFeaturesCache = []; // Array of manually placed features {type, x, y}  sent to backend on subdivide
let currentParcelData = null; // Full response from the last getPlotDetailsAndInspection call
let currentGisCode = "";      // GIS code for the selected village sheet (e.g., "GI10XXXXX")
let currentLevels = "";       // 7-level comma-delimited code string (e.g., "09,01,05,003,01,01,01,")
let currentExtent = null;     // Bounding box {xmin, ymin, xmax, ymax} of selected sheet

// Bihar GIS Layer references (ArcGIS MapServer tile layers)
let nhRoadsLayer, shRoadsLayer, mdrRoadsLayer, villageRoadsLayer, riversLayer, streamsLayer;
let bungalowsLayer, restHouseLayer, officesLayer, residentLayer;
let templeLayer, mosqueLayer, churchLayer, gurudwaraLayer, policeStationLayer;
let fullRoadsLayer, panchayatBoundariesLayer, villageBoundariesLayer;

// Basemap layer references (ArcGIS satellite + streets)
let arcgisHybridBase, arcgisHybridLabels, arcgisStreetsBase;

const stateCode = "10"; // Bihar State Code (fixed  this app only covers Bihar)

// --- STEP 3: Entry Point ---
// jQuery $(document).ready() fires after the HTML DOM is fully loaded.
// Calls three initialization functions in order:
//   1. initMap()            Create the OpenLayers map with all layers
//   2. initDropdowns()      Trigger the first dropdown (District list)
//   3. setupEventListeners()  Attach all UI event handlers
$(document).ready(function() {
    initMap();
    initDropdowns();
    setupEventListeners();
});

const vectorStyleFunction = function(feature) {
    const styles = [];
    const geom = feature.getGeometry();
    if (!geom) return styles;
    
    // 1. Polygon Outline & Fill
    styles.push(new ol.style.Style({
        stroke: new ol.style.Stroke({
            color: '#ff3366',
            width: 3
        }),
        fill: new ol.style.Fill({
            color: 'rgba(255, 51, 102, 0.15)'
        })
    }));
    
    const coordinates = geom.getCoordinates()[0];
    if (coordinates) {
        // 2. Vertex markers (small circles at points)
        coordinates.forEach((coord, index) => {
            if (index === coordinates.length - 1) return; // skip closing coord
            styles.push(new ol.style.Style({
                geometry: new ol.geom.Point(coord),
                image: new ol.style.Circle({
                    radius: 5,
                    fill: new ol.style.Fill({
                        color: '#ffffff'
                    }),
                    stroke: new ol.style.Stroke({
                        color: '#ff3366',
                        width: 2
                    })
                })
            }));
        });
        
        // 3. Side length labels (midpoints)
        const segmentLengths = feature.get('segment_lengths');
    const riverAdjacentSegments = feature.get('river_adjacent_segments') || [];
    
    if (segmentLengths) {
        for (let i = 0; i < coordinates.length - 1; i++) {
            const p1 = coordinates[i];
            const p2 = coordinates[i+1];
            
            // Highlight river adjacency with a thick blue line
            if (riverAdjacentSegments.includes(i)) {
                styles.push(new ol.style.Style({
                    geometry: new ol.geom.LineString([p1, p2]),
                    stroke: new ol.style.Stroke({
                        color: 'rgba(0, 191, 255, 0.8)', // Deep sky blue
                        width: 6
                    }),
                    text: new ol.style.Text({
                        text: `River Adj (${feature.get('river_name')})`,
                        font: 'bold 12px Inter, sans-serif',
                        fill: new ol.style.Fill({ color: '#00bfff' }),
                        stroke: new ol.style.Stroke({ color: '#fff', width: 3 }),
                        placement: 'line',
                        offsetY: 15
                    })
                }));
            }
            
            const dx = p2[0] - p1[0];              const midX = (p1[0] + p2[0]) / 2;
                const midY = (p1[1] + p2[1]) / 2;
                
                const lenVal = segmentLengths[i];
                const labelText = lenVal ? `${lenVal.toFixed(1)}m` : '';
                
                styles.push(new ol.style.Style({
                    geometry: new ol.geom.Point([midX, midY]),
                    text: new ol.style.Text({
                        text: labelText,
                        font: 'bold 11px Outfit, sans-serif',
                        fill: new ol.style.Fill({ color: '#ffffff' }),
                        stroke: new ol.style.Stroke({ color: '#000000', width: 3 }),
                        offsetY: -8,
                        placement: 'point'
                    })
                }));
            }
        }
    }
    return styles;
};

// =============================================================================
// STEP 4: Map Initialization
// =============================================================================
// Creates all OpenLayers map layers and the main Map object.
// Layer render order (bottom to top):
//   1. ArcGIS Satellite Basemap (Imagery)
//   2. ArcGIS Satellite Labels (Boundaries & Place Names)
//   3. ArcGIS Streets Basemap (for overlay mode)
//   4. Bihar GIS Infrastructure Layers (Roads, Rivers, POIs) from ArcGIS MapServer
//   5. BhuNaksha WMS Cadastral Map (plotLyr)  the government cadastral overlay
//   6. BhuNaksha WMS Selected Plot Highlight (selPlotLyr)
//   7. osmVectorLayer  Manually placed trees/wells (client-side)
//   8. vectorLayer  Selected parcel polygon boundary (pink outline)
//   9. kurraVectorLayer  Kurra sub-division polygons (colored fills)
//
// All WMS calls go through the Flask proxy at /proxy/WMS
// (browser cannot directly call BhuNaksha due to CORS)
// =============================================================================
function initMap() {
    // ArcGIS Hybrid Basemap (Imagery + Labels)
    arcgisHybridBase = new ol.layer.Tile({
        title: "ArcGIS Satellite",
        type: "base",
        visible: true,
        source: new ol.source.XYZ({
            url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attributions: "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
            maxZoom: 17 // Lowered to 17 to guarantee we stretch the last valid image instead of getting Esri's "Map data not yet available" grey tile
        })
    });
    arcgisHybridLabels = new ol.layer.Tile({
        title: "ArcGIS Labels",
        visible: true,
        source: new ol.source.XYZ({
            url: "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
            maxZoom: 17
        })
    });

    // ArcGIS Streets Basemap
    arcgisStreetsBase = new ol.layer.Tile({
        title: "ArcGIS Streets",
        type: "base",
        visible: false,
        source: new ol.source.XYZ({
            url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
            attributions: "Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ, USGS, Intermap, iPC, NRCAN, METI, TomTom, and others"
        })
    });



    // Bihar GIS Native Layers
    nhRoadsLayer = new ol.layer.Tile({
        title: "National Highways",
        visible: true,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/NHRoads/MapServer"
        })
    });
    shRoadsLayer = new ol.layer.Tile({
        title: "State Highways",
        visible: true,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/SHRoads/MapServer"
        })
    });
    mdrRoadsLayer = new ol.layer.Tile({
        title: "Major District Roads",
        visible: true,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/MDR/MapServer"
        })
    });
    villageRoadsLayer = new ol.layer.Tile({
        title: "Village Roads",
        visible: true,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RWD/Village_Road/MapServer"
        })
    });
    riversLayer = new ol.layer.Tile({
        title: "Rivers",
        visible: true,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/WATER_IRRIGATION/Rivers/MapServer"
        })
    });
    streamsLayer = new ol.layer.Tile({
        title: "Streams",
        visible: true,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/WATER_IRRIGATION/IrrigationStreams/MapServer"
        })
    });

    // Bungalows & Rest Houses
    bungalowsLayer = new ol.layer.Tile({
        title: "Bungalows",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/Inspection_Bunglow/MapServer"
        })
    });
    restHouseLayer = new ol.layer.Tile({
        title: "Rest Houses",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/Rest_House/MapServer"
        })
    });

    // Gov Offices & Residents
    officesLayer = new ol.layer.Tile({
        title: "Offices",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/Office/MapServer"
        })
    });
    residentLayer = new ol.layer.Tile({
        title: "Residents",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/Resident/MapServer"
        })
    });

    // Places of Worship (Temple, Mosque, Church, Gurudwara)
    templeLayer = new ol.layer.Tile({
        title: "Temples",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/HOME_DEPT/Temple/MapServer"
        })
    });
    mosqueLayer = new ol.layer.Tile({
        title: "Mosques",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/HOME_DEPT/Mosque/MapServer"
        })
    });
    churchLayer = new ol.layer.Tile({
        title: "Churches",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/HOME_DEPT/Church/MapServer"
        })
    });
    gurudwaraLayer = new ol.layer.Tile({
        title: "Gurudwaras",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/HOME_DEPT/Gurudwara/MapServer"
        })
    });

    // Police Stations
    policeStationLayer = new ol.layer.Tile({
        title: "Police Stations",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/HOME_DEPT/POLICE_STATION/MapServer"
        })
    });

    // Full Bihar Road Network
    fullRoadsLayer = new ol.layer.Tile({
        title: "Bihar Road Network",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/RCD_DEPT/ROAD_BIHAR/MapServer"
        })
    });

    // Panchayat & Village Boundaries
    panchayatBoundariesLayer = new ol.layer.Tile({
        title: "Panchayat Boundaries",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/AdministrativrBoundaries/Panchayat/MapServer"
        })
    });
    villageBoundariesLayer = new ol.layer.Tile({
        title: "Village Boundaries",
        visible: false,
        source: new ol.source.TileArcGISRest({
            url: "https://gisserver.bihar.gov.in/arcgis/rest/services/AdministrativrBoundaries/Village/MapServer"
        })
    });

    // BhuNaksha WMS Village Map layer (transparent overlay)
    // BhuNaksha WMS Village Map layer (transparent overlay)
    plotLyr = new ol.layer.Tile({
        title: "Cadastral Map",
        visible: false,
        opacity: 0.6,
        source: new ol.source.TileWMS({
            url: API_BASE_URL + "/proxy/WMS",
            params: {
                "LAYERS": "VILLAGE_MAP",
                "transparent": "TRUE",
                "state": stateCode,
                "SRS": "EPSG:4326",
                "VERSION": "1.1.1",
                "gis_code": ""
            },
            serverType: "geoserver"
        })
    });

    // BhuNaksha WMS Selected Plot Highlight layer
    selPlotLyr = new ol.layer.Tile({
        title: "Selected Plot",
        visible: false,
        opacity: 0.8,
        source: new ol.source.TileWMS({
            url: API_BASE_URL + "/proxy/WMS",
            params: {
                "LAYERS": "PLOT_LIST",
                "transparent": "TRUE",
                "state": stateCode,
                "SRS": "EPSG:4326",
                "VERSION": "1.1.1",
                "gis_code": "",
                "plot_id": "",
                "STYLES": "PLOT_SELECTION"
            },
            serverType: "geoserver"
        })
    });



    vectorSource = new ol.source.Vector();
    vectorLayer = new ol.layer.Vector({
        source: vectorSource,
        style: vectorStyleFunction
    });

    // OSM Nearby Features Layer
    osmVectorLayer = new ol.layer.Vector({
        source: new ol.source.Vector(),
        style: function(feature) {
            const props = feature.getProperties();
            if (props.natural === 'tree' || props.natural === 'wood' || props.landuse === 'orchard') {
                return new ol.style.Style({
                    image: new ol.style.Circle({
                        radius: 6,
                        fill: new ol.style.Fill({ color: '#228B22' }),
                        stroke: new ol.style.Stroke({ color: '#fff', width: 1 })
                    })
                });
            } else if (props.man_made === 'water_well' || props.natural === 'water') {
                return new ol.style.Style({
                    image: new ol.style.Circle({
                        radius: 6,
                        fill: new ol.style.Fill({ color: '#1E90FF' }),
                        stroke: new ol.style.Stroke({ color: '#fff', width: 1 })
                    })
                });
            }
            return new ol.style.Style({
                stroke: new ol.style.Stroke({ color: '#aaaaaa', width: 1 })
            });
        }
    });

    // Kurra Subdivision Layer
    kurraVectorLayer = new ol.layer.Vector({
        source: new ol.source.Vector(),
        style: function(feature) {
            const id = feature.get('sub_plot_id') || 1;
            const colors = ['rgba(255,0,0,0.4)', 'rgba(0,255,0,0.4)', 'rgba(0,0,255,0.4)', 'rgba(255,255,0,0.4)', 'rgba(255,0,255,0.4)'];
            const color = colors[(id - 1) % colors.length];
            return new ol.style.Style({
                fill: new ol.style.Fill({ color: color }),
                stroke: new ol.style.Stroke({ color: '#000', width: 2 }),
                text: new ol.style.Text({
                    text: `Plot ${id}`,
                    font: 'bold 12px sans-serif',
                    fill: new ol.style.Fill({ color: '#000' }),
                    stroke: new ol.style.Stroke({ color: '#fff', width: 2 })
                })
            });
        }
    });

    // Setup map view centered on Patna, Bihar
    map = new ol.Map({
        target: "map",
        layers: [
            arcgisHybridBase, arcgisHybridLabels, arcgisStreetsBase,
            nhRoadsLayer, shRoadsLayer, mdrRoadsLayer, villageRoadsLayer, riversLayer, streamsLayer,
            bungalowsLayer, restHouseLayer, officesLayer, residentLayer,
            templeLayer, mosqueLayer, churchLayer, gurudwaraLayer, policeStationLayer,
            fullRoadsLayer, panchayatBoundariesLayer, villageBoundariesLayer,
            plotLyr, selPlotLyr, osmVectorLayer, vectorLayer, kurraVectorLayer
        ],
        view: new ol.View({
            projection: "EPSG:4326",
            center: [85.1376, 25.5941], // Patna GPS Coordinates
            zoom: 7,
            minZoom: 5,                  // Restrict zoom out to keep focus on India
            extent: [68.0, 6.0, 98.0, 38.0] // Bounding box for India
        })
    });
}

// =============================================================================
// STEP 5: Dropdown Initialization
// =============================================================================
// Fetches Level 0 data (Bihar Districts) from the backend on page load.
// This triggers the cascading dropdown chain:
//   Level 0  District list
//   Level 1  Sub-Division list (after district selected)
//   Level 2  Circle list (after subdiv selected)
//   Level 3  Mouza/Village list (after circle selected)
//   Level 4  Survey Type list (after mouza selected)
//   Level 5  Map Instance list (after survey type selected)
//   Level 6  Sheet list (after map instance selected)
// When Sheet is selected  loadVillageSheet() is called
// =============================================================================
function initDropdowns() {
    fetchDropdown(0, ""); // Fetch Level 0 -> District List
}

// =============================================================================
// STEP 6: Dropdown Fetch Logic
// =============================================================================
// Fetches dropdown options for a given level from the backend.
// PROCESS:
//   1. POST /proxy/Levels/ListsAfterLevel with {state, level, codes}
//   2. Backend checks disk cache or proxies to BhuNaksha
//   3. Backend returns [{code, value}] array
//   4. Frontend populates the corresponding <select> element
//   5. Enables the select and disables all downstream selects
//
// CALLED BY:
//   - initDropdowns() for the initial district list
//   - Each dropdown .change() handler when user makes a selection
// =============================================================================
function fetchDropdown(level, parentCodes) {
    showLoading(true, `Loading administrative levels...`);
    
    $.post(API_BASE_URL + "/proxy/Levels/ListsAfterLevel", {
        state: stateCode,
        level: level,
        codes: parentCodes,
        hasmap: "true"
    }, function(data) {
        showLoading(false);
        if (!data || data.length === 0) return;

        // The dropdown data is returned at index 0 of the response list
        const options = data[0];
        const targetSelectId = getSelectIdForLevel(level + 1);
        const $select = $(`#${targetSelectId}`);
        
        $select.empty().append(`<option value="">--Select ${getLevelLabel(level + 1)}--</option>`);
        
        options.forEach(item => {
            $select.append($("<option></option>").attr("value", item.code).text(item.value));
        });

        $select.prop("disabled", false);
    }, "json").fail(function(xhr, status, error) {
        showLoading(false);
        showToast("Error loading level options", "error");
    });
}

// =============================================================================
// STEP 7: Event Listeners
// =============================================================================
// Wires all UI controls to their handler functions.
// Major event categories:
//   A. Dropdown Cascade   each dropdown change triggers the next level fetch
//   B. Basemap & Layers   checkboxes toggle Bihar GIS layer visibility
//   C. Map Click          singleclick queries clicked GPS coordinate for plot number
//   D. PNIU Search        searches plot by 14-digit PNIU code
//   E. Sheet Clone        batch-scrapes all plots in the selected sheet
//   F. Kurra Division     runs AI land subdivision on the selected plot
//   G. PDF Report         generates and downloads the Kurra PDF report
//   H. Map Drawing Tools  enables tree/well placement mode on the map
// =============================================================================
function setupEventListeners() {
    // Opacity Slider
    $("#opacity-slider").on("input", function() {
        const val = $(this).val();
        $("#opacity-value").text(`${val}%`);
        if (plotLyr) {
            plotLyr.setOpacity(val / 100);
        }
    });

    // Sidebar Toggles
    $("#sidebar-toggle-collapse").click(function() {
        $("#sidebar").addClass("collapsed");
        $("#sidebar-toggle-float").show();
    });

    $("#sidebar-toggle-float").click(function() {
        $("#sidebar").removeClass("collapsed");
        $(this).hide();
    });

    // --- A. Dropdown Cascade ---
    // Each dropdown change re-fetches the next level using accumulated parent codes.
    // The codes string is built by concatenating parent codes with commas.
    // Example: District "09" + Subdiv "01" = codes "09,01," for Circle lookup
    $("#select-district").change(function() {
        resetDropdownsFrom(2);
        const val = $(this).val();
        if (val) fetchDropdown(1, `${val},`);
    });

    $("#select-subdiv").change(function() {
        resetDropdownsFrom(3);
        const val = $(this).val();
        const dist = $("#select-district").val();
        if (val) fetchDropdown(2, `${dist},${val},`);
    });

    $("#select-circle").change(function() {
        resetDropdownsFrom(4);
        const val = $(this).val();
        const dist = $("#select-district").val();
        const subdiv = $("#select-subdiv").val();
        if (val) fetchDropdown(3, `${dist},${subdiv},${val},`);
    });

    $("#select-mouza").change(function() {
        resetDropdownsFrom(5);
        const val = $(this).val();
        const dist = $("#select-district").val();
        const subdiv = $("#select-subdiv").val();
        const circle = $("#select-circle").val();
        if (val) fetchDropdown(4, `${dist},${subdiv},${circle},${val},`);
    });

    $("#select-survey").change(function() {
        resetDropdownsFrom(6);
        const val = $(this).val();
        const dist = $("#select-district").val();
        const subdiv = $("#select-subdiv").val();
        const circle = $("#select-circle").val();
        const mouza = $("#select-mouza").val();
        if (val) fetchDropdown(5, `${dist},${subdiv},${circle},${mouza},${val},`);
    });

    $("#select-mapinst").change(function() {
        resetDropdownsFrom(7);
        const val = $(this).val();
        const dist = $("#select-district").val();
        const subdiv = $("#select-subdiv").val();
        const circle = $("#select-circle").val();
        const mouza = $("#select-mouza").val();
        const survey = $("#select-survey").val();
        if (val) fetchDropdown(6, `${dist},${subdiv},${circle},${mouza},${survey},${val},`);
    });

    // Final Sheet selection triggers loading map
    $("#select-sheet").change(function() {
        const val = $(this).val();
        if (val) {
            loadVillageSheet();
        }
    });



    // Bihar GIS Layer Toggles
    $("#chk-nh-roads").change(function() {
        nhRoadsLayer.setVisible($(this).is(":checked"));
    });
    $("#chk-sh-roads").change(function() {
        const checked = $(this).is(":checked");
        shRoadsLayer.setVisible(checked);
        mdrRoadsLayer.setVisible(checked);
    });
    $("#chk-village-roads").change(function() {
        villageRoadsLayer.setVisible($(this).is(":checked"));
    });
    $("#chk-rivers").change(function() {
        const checked = $(this).is(":checked");
        riversLayer.setVisible(checked);
        streamsLayer.setVisible(checked);
    });
    $("#chk-bungalows").change(function() {
        const checked = $(this).is(":checked");
        bungalowsLayer.setVisible(checked);
        restHouseLayer.setVisible(checked);
    });
    $("#chk-offices").change(function() {
        const checked = $(this).is(":checked");
        officesLayer.setVisible(checked);
        residentLayer.setVisible(checked);
    });
    $("#chk-worship").change(function() {
        const checked = $(this).is(":checked");
        templeLayer.setVisible(checked);
        mosqueLayer.setVisible(checked);
        churchLayer.setVisible(checked);
        gurudwaraLayer.setVisible(checked);
    });
    $("#chk-security").change(function() {
        policeStationLayer.setVisible($(this).is(":checked"));
    });

    $("#chk-full-roads").change(function() {
        fullRoadsLayer.setVisible($(this).is(":checked"));
    });
    $("#chk-panchayat-boundaries").change(function() {
        panchayatBoundariesLayer.setVisible($(this).is(":checked"));
    });
    $("#chk-village-boundaries").change(function() {
        villageBoundariesLayer.setVisible($(this).is(":checked"));
    });

    function updateBasemaps() {
        const type = $("input[name='basemap_type']:checked").val();
        const streetOpacity = parseFloat($("#street-opacity-slider").val()) / 100.0;

        // Reset visibility and opacity defaults
        arcgisHybridBase.setVisible(false);
        arcgisHybridLabels.setVisible(false);
        arcgisStreetsBase.setVisible(false);
        arcgisStreetsBase.setOpacity(1.0);

        if (type === 'satellite') {
            arcgisHybridBase.setVisible(true);
            arcgisHybridLabels.setVisible(true);
            
            // If the slider is > 0, overlay streets on top of satellite
            if (streetOpacity > 0) {
                arcgisStreetsBase.setVisible(true);
                arcgisStreetsBase.setOpacity(streetOpacity);
            }
        } else if (type === 'streets') {
            arcgisStreetsBase.setVisible(true);
        }
    }

    // Basemap Type Switcher
    $("input[name='basemap_type']").change(function() {
        updateBasemaps();
    });

    // Street Overlay Opacity Slider
    $("#street-opacity-slider").on("input", function() {
        const val = $(this).val();
        $("#street-opacity-value").text(`${val}%`);
        updateBasemaps();
    });

    // PDF Modal view button
    $("#btn-view-ldm").click(function() {
        if (currentParcelData && currentParcelData.report && currentParcelData.report.url) {
            $("#pdf-iframe").attr("src", currentParcelData.report.url);
            $("#pdf-modal").addClass("active");
        }
    });

    // Close PDF modal
    $("#btn-close-pdf").click(function() {
        $("#pdf-modal").removeClass("active");
        $("#pdf-iframe").attr("src", "");
    });
    
    // Close modal on background click
    $("#pdf-modal").click(function(e) {
        if (e.target === this) {
            $(this).removeClass("active");
            $("#pdf-iframe").attr("src", "");
        }
    });

    // PDF Download button
    $("#btn-download-ldm").click(function() {
        if (currentParcelData && currentParcelData.report && currentParcelData.report.url) {
            window.open(currentParcelData.report.url, "_blank");
        }
    });

    // Export GeoJSON
    $("#btn-export-geojson").click(function() {
        if (currentParcelData && currentParcelData.parcel) {
            const url = API_BASE_URL + `/proxy/Export/GeoJSON/${currentParcelData.parcel.plot_no}?parcel_id=${currentParcelData.parcel.id}`;
            window.open(url, "_blank");
        }
    });

    // Export CSV
    $("#btn-export-csv").click(function() {
        if (currentParcelData && currentParcelData.parcel) {
            const url = API_BASE_URL + `/proxy/Export/CSV/${currentParcelData.parcel.plot_no}?parcel_id=${currentParcelData.parcel.id}`;
            window.open(url, "_blank");
        }
    });

    // --- C. Map Click-to-Select Parcel ---
    // PROCESS:
    //   1. User clicks on the map
    //   2. OpenLayers returns the GPS coordinate [lon, lat] of the click
    //   3. POST /proxy/MapInfo/getPlotAtGPS with the GPS coordinate
    //   4. Backend converts GPS  UTM, queries BhuNaksha or SQLite
    //   5. Returns {kide: "plot_no"}  the plot number at that point
    //   6. Frontend calls selectPlotByNumber(plot_no) to load full details
    // NOTE: This requires the sheet to be selected first (currentGisCode must be set)
    map.on("singleclick", function(evt) {
        if (!currentGisCode) {
            showToast("Please select a location sheet first", "warning");
            return;
        }

        const coords = evt.coordinate; // [lon, lat]
        const lon = coords[0];
        const lat = coords[1];

        showLoading(true, "Querying clicked parcel coordinates...");

        $.post(API_BASE_URL + "/proxy/MapInfo/getPlotAtGPS", {
            state: stateCode,
            giscode: currentGisCode,
            levels: currentLevels,
            lon: lon,
            lat: lat
        }, function(data) {
            showLoading(false);
            if (data && data.kide) {
                selectPlotByNumber(data.kide);
            } else {
                showToast("No parcel found at clicked coordinates", "warning");
            }
        }, "json").fail(function(xhr) {
            showLoading(false);
            const err = xhr.responseJSON ? xhr.responseJSON.error : "Failed to query clicked coordinates";
            showToast(err, "error");
        });
    });

    // --- D. PNIU Search ---
    // Searches for a plot by its 14-digit PNIU (Parcel Number Identification Unit) code.
    // PROCESS:
    //   1. User enters PNIU and clicks search (or presses Enter)
    //   2. executePniuSearch() POSTs to /proxy/MapInfo/getPointsfromPNIU
    //   3. Backend returns comma-delimited string; frontend extracts plot_no (field 5)
    //   4. selectPlotByNumber(plotNo) is called to load full details
    $("#btn-search-pniu").click(executePniuSearch);
    $("#search-pniu").keypress(function(e) {
        if (e.which === 13) executePniuSearch();
    });

    // --- E. Map Image Download ---
    // Constructs a WMS GetMap URL for the selected sheet and opens it in a new tab.
    // The browser downloads a 2048x1536 high-resolution PNG of the cadastral map.
    $("#btn-download-map").click(function() {
        if (!currentGisCode || !currentExtent) {
            showToast("Please select a map sheet to scrape", "warning");
            return;
        }

        // Construct a direct WMS GetMap download link for the user
        const bboxStr = `${currentExtent.xmin},${currentExtent.ymin},${currentExtent.xmax},${currentExtent.ymax}`;
        const downloadUrl = API_BASE_URL + `/proxy/WMS?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=VILLAGE_MAP&transparent=true&state=${stateCode}&SRS=EPSG:4326&gis_code=${currentGisCode}&BBOX=${bboxStr}&WIDTH=2048&HEIGHT=1536&FORMAT=image/png`;
        
        // Open download link in a new tab
        window.open(downloadUrl, "_blank");
        showToast("Scraping high-resolution WMS image...", "success");
    });

    // --- F. Sheet Clone / Batch Scraper ---
    // AUTO-DISCOVERS and caches all plot geometries in the selected cadastral sheet.
    // PROCESS (looped batch approach):
    //   1. Generate a uniform UTM grid (35m spacing) over the sheet bounding box
    //   2. For each batch of 65 grid points: POST /api/sheet/scrape_batch
    //   3. Backend skips points inside known polygons, queries new ones
    //   4. Frontend loops (batch_index++) until is_done=true
    //   5. Redirects to /api/sheet/export_geojson to download the complete GeoJSON
    // NOTE: Can take several minutes for large sheets with many plots.
    $("#btn-clone-sheet").click(function() {
        if (!currentGisCode || !currentLevels) {
            showToast("Please select a map sheet to scrape", "warning");
            return;
        }

        const gridStep = 35.0; // 35 meter spacing is standard
        const batchSize = 65;  // process 65 grid points per batch
        let currentBatch = 0;
        
        function runBatch() {
            showLoading(true, `Cloning vector data: Batch ${currentBatch + 1} in progress...`);
            $.ajax({
                url: API_BASE_URL + "/api/sheet/scrape_batch",
                type: "POST",
                contentType: "application/json",
                data: JSON.stringify({
                    state: stateCode,
                    giscode: currentGisCode,
                    levels: currentLevels,
                    batch_index: currentBatch,
                    batch_size: batchSize,
                    grid_step: gridStep
                }),
                success: function(res) {
                    if (res.success) {
                        const totalScanned = currentBatch * batchSize + res.scanned_points_in_batch;
                        const pct = Math.min(100, Math.round((totalScanned / res.total_points) * 100));
                        showToast(`Scraped batch ${currentBatch + 1} (${pct}%). Found ${res.new_plots_found.length} new plots. Total plots saved: ${res.total_plots_saved}`, "success");
                        
                        if (res.is_done) {
                            showLoading(true, "Compiling GeoJSON features for download...");
                            // All batches done! Redirect to export GeoJSON
                            window.location.href = API_BASE_URL + `/api/sheet/export_geojson?state=${stateCode}&levels=${encodeURIComponent(currentLevels)}`;
                            showLoading(false);
                            showToast("Sheet vector clone download initiated successfully!", "success");
                        } else {
                            currentBatch++;
                            runBatch(); // trigger next batch
                        }
                    } else {
                        showLoading(false);
                        showToast("Batch scraping failed: " + (res.error || "Unknown error"), "error");
                    }
                },
                error: function(xhr, status, error) {
                    showLoading(false);
                    showToast("Error processing batch scrape: " + error, "error");
                }
            });
        }
        
        // Start the batch scraping process
        runBatch();
    });
    
    // --- G. Kurra Division Event Listeners ---
    // Toggles between "Equal Share" and "Custom Share" input modes.
    $("input[name='share_type']").change(function() {
        if ($(this).val() === 'equal') {
            $("#equal-share-group").show();
            $("#custom-share-group").hide();
        } else {
            $("#equal-share-group").hide();
            $("#custom-share-group").show();
        }
    });

    // Load nearby button logic removed as we use native ArcGIS MapServer layers.
    
    let drawInteraction = null;
    let selectInteraction = null;

    function enableDraw(type) {
        if (drawInteraction) map.removeInteraction(drawInteraction);
        if (selectInteraction) map.removeInteraction(selectInteraction);
        
        drawInteraction = new ol.interaction.Draw({
            source: osmVectorLayer.getSource(),
            type: type
        });
        
        drawInteraction.on('drawend', function(e) {
            const f = e.feature;
            const objType = prompt("What is this object? (e.g. tree, well)", "tree");
            if (!objType) {
                setTimeout(() => osmVectorLayer.getSource().removeFeature(f), 10);
                return;
            }
                if (objType.toLowerCase() === 'tree') {
                    f.setProperties({ natural: 'tree', manual: true });
                } else if (objType.toLowerCase() === 'well') {
                    f.setProperties({ man_made: 'water_well', manual: true });
                } else {
                    f.setProperties({ custom_type: objType, manual: true });
                }
                showToast(objType + " added", "success");
            
            map.removeInteraction(drawInteraction);
            drawInteraction = null;
            updateOsmCache();
        });
        
        map.addInteraction(drawInteraction);
        showToast("Click on map to place object", "info");
    }

    function enableDelete() {
        if (drawInteraction) map.removeInteraction(drawInteraction);
        if (selectInteraction) map.removeInteraction(selectInteraction);
        
        selectInteraction = new ol.interaction.Select({
            layers: [osmVectorLayer]
        });
        
        selectInteraction.on('select', function(e) {
            if (e.selected.length > 0) {
                const f = e.selected[0];
                if (confirm("Delete this object?")) {
                    osmVectorLayer.getSource().removeFeature(f);
                    updateOsmCache();
                    showToast("Object deleted", "success");
                }
                selectInteraction.getFeatures().clear();
            }
        });
        
        map.addInteraction(selectInteraction);
        showToast("Click on an object to delete it", "info");
    }

    function updateOsmCache() {
        osmFeaturesCache = [];
        let treeCount = 0, wellCount = 0;
        
        osmVectorLayer.getSource().getFeatures().forEach(f => {
            const props = f.getProperties();
            const geom = f.getGeometry();
            if (geom.getType() === 'Point') {
                const coords = geom.getCoordinates();
                let fType = props.natural === 'tree' ? 'tree' : (props.man_made === 'water_well' ? 'well' : props.custom_type || 'custom');
                if (fType === 'tree') treeCount++;
                if (fType === 'well') wellCount++;
                osmFeaturesCache.push({
                    type: fType,
                    x: coords[0],
                    y: coords[1]
                });
            }
        });
        
        if (osmFeaturesCache.length > 0) {
            $("#feature-counts").html(`Trees: <b>${treeCount}</b> | Wells: <b>${wellCount}</b>`);
            $("#feature-summary").show();
        } else {
            $("#feature-summary").hide();
        }
    }

    $("#btn-add-tree").click(() => enableDraw('Point'));
    $("#btn-add-well").click(() => enableDraw('Point'));
    $("#btn-delete-obj").click(() => enableDelete());

    // --- H. AI Kurra Division ---
    // Core feature: AI-assisted land subdivision calculation.
    // PROCESS:
    //   1. User sets share percentages and optionally places trees/wells on the map
    //   2. #btn-subdivide click: validate shares, collect osmFeaturesCache
    //   3. POST /api/parcel/{plot_no}/subdivide with JSON body:
    //      {shares: [...], features: osmFeaturesCache, parcel_info: currentParcelData.parcel}
    //   4. Backend: loads parcel from SQLite, queries ArcGIS for roads/rivers,
    //      runs subdivide.py algorithms, consults LLM, returns GeoJSON FeatureCollection
    //   5. Frontend: draws colored sub-polygons on kurraVectorLayer
    //   6. Displays AI strategy name + LLM explanation in the sidebar
    //   7. Hides original vectorLayer so subdivisions are clearly visible
    // See: backend/app.py subdivide_parcel() and backend/subdivide.py for algorithm details
    $("#btn-subdivide").click(function() {
        if (!currentParcelData || !currentParcelData.parcel) return;
        const plotNo = currentParcelData.parcel.plot_no;
        const parcelId = currentParcelData.parcel.id;
        
        let shares = [];
        const type = $("input[name='share_type']:checked").val();
        if (type === 'equal') {
            const count = parseInt($("#num-sharers").val());
            if (isNaN(count) || count < 2) return showToast("Invalid number of sharers", "warning");
            shares = Array(count).fill(100.0 / count);
        } else {
            const str = $("#custom-shares").val();
            shares = str.split(",").map(s => parseFloat(s.trim()));
            if (shares.some(isNaN)) return showToast("Invalid custom shares format", "warning");
            const sum = shares.reduce((a,b)=>a+b, 0);
            if (Math.abs(sum - 100) > 0.1) return showToast("Shares must sum to 100", "warning");
        }
        
        // We need to pass cached features
        const payload = {
            shares: shares,
            features: osmFeaturesCache,
            parcel_info: currentParcelData.parcel
        };
        
        showLoading(true, "Calculating Kurra Division...");
        $.ajax({
            url: API_BASE_URL + `/api/parcel/${plotNo}/subdivide?parcel_id=${parcelId}`,
            type: "POST",
            contentType: "application/json",
            data: JSON.stringify(payload),
            success: function(res) {
                showLoading(false);
                if (res && res.type === "FeatureCollection") {
                    kurraVectorLayer.getSource().clear();
                    const format = new ol.format.GeoJSON();
                    
                    // We need to shift the features by the user's offset
                    let htmlContent = "";
                    
                    if (res.strategy_name) {
                        // Save for PDF generation
                        window.lastStrategyName = res.strategy_name;
                        window.lastLlmExplanation = res.llm_explanation;
                        window.lastLlmFailed = res.llm_failed;
                        // Save road frontage for PDF report if present in response
                        window.roadFrontage = res.frontage_coords || [];
                        
                        htmlContent += `<div style="background: rgba(255, 51, 102, 0.1); border-left: 3px solid #ff3366; padding: 10px; margin-bottom: 15px; border-radius: 4px;">
                          <strong style="color: #ff3366;">Strategy: ${res.strategy_name}</strong>
                          <p style="margin: 5px 0 0 0; font-size: 0.9em; line-height: 1.4;">${res.llm_explanation || ""}</p>
                          ${res.llm_failed ? '<div style="color: #ffa500; font-size: 0.8em; margin-top: 5px;"> LLM Service Offline - Used mathematical fallback.</div>' : ''}
                        </div>`;
                    }

                    res.features.forEach(feat => {
                        const coords = feat.geometry.coordinates[0];
                        const olFeat = new ol.Feature({ geometry: new ol.geom.Polygon([coords]) });
                        olFeat.setProperties(feat.properties);
                        kurraVectorLayer.getSource().addFeature(olFeat);
                        
                        // Build results HTML
                        const props = feat.properties;
                        const featsStr = props.contained_features.length > 0 
                            ? props.contained_features.map(f => f.type).join(', ') 
                            : 'None';
                        htmlContent += `
                            <div style="margin-bottom: 0.5rem; padding-bottom: 0.5rem; border-bottom: 1px dashed var(--border-color);">
                                <strong style="color: #ff3366;">Sub-Plot ${props.sub_plot_id} (${props.share_percentage.toFixed(1)}%)</strong><br>
                                <span>Area: ${(props.area_sqm / 4046.8564).toFixed(3)} acres (${props.area_sqm.toFixed(1)} m²)</span><br>
                                <span>Perimeter: ${props.perimeter_m.toFixed(1)} m</span><br>
                                <span style="color: #228B22; font-weight: bold;">Contained Objects: ${featsStr}</span>
                            </div>
                        `;
                    });
                    
                    $("#kurra-results-content").html(htmlContent);
                    $("#kurra-results").show();
                    
                    showToast("Division calculated successfully", "success");
                    
                    // Hide original polygon to see split
                    vectorLayer.setVisible(false);
                    
                } else {
                    showToast("Error in subdivision output", "error");
                }
            },
            error: function(xhr) {
                showLoading(false);
                showToast("Subdivision failed: " + (xhr.responseJSON?.error || "Unknown"), "error");
            }
        });
    });

    // --- I. PDF Kurra Report Download ---
    // Generates and downloads the official PDF Kurra Division Report.
    // PROCESS:
    //   1. Collect all sub-plot geometries from kurraVectorLayer (OpenLayers features)
    //   2. Clean properties (remove non-serializable OpenLayers geometry objects)
    //   3. POST /api/parcel/{plot_no}/generate_report with JSON body:
    //      {features: osmFeaturesCache, subdivisions: [...], frontage: [...], parcel_info: ...}
    //   4. Backend: calls report_generator.generate_kurra_report()  returns PDF bytes
    //   5. Frontend: receives binary blob, creates Object URL, triggers <a> download
    // NOTE: Uses xhrFields: {responseType: 'blob'} to receive binary PDF data correctly.
    $("#btn-download-kurra-report").click(function() {
        if (!currentParcelData || !currentParcelData.parcel) return;
        const plotNo = currentParcelData.parcel.plot_no;
        const parcelId = currentParcelData.parcel.id;
        
        const subPlots = [];
        kurraVectorLayer.getSource().getFeatures().forEach(f => {
            const rawProps = f.getProperties();
            const geom = f.getGeometry();
            // Only extract the data properties we need  exclude OpenLayers geometry object
            const cleanProps = {
                sub_plot_id: rawProps.sub_plot_id,
                share_percentage: rawProps.share_percentage,
                area_sqm: rawProps.area_sqm,
                perimeter_m: rawProps.perimeter_m,
                frontage_m: rawProps.frontage_m || 0,
                contained_features: rawProps.contained_features || []
            };
            subPlots.push({
                properties: cleanProps,
                geometry: {
                    type: 'Polygon',
                    coordinates: [geom.getCoordinates()[0]]
                }
            });
        });
        
        currentParcelData.parcel.llm_explanation = window.lastLlmExplanation;
        currentParcelData.parcel.strategy_name = window.lastStrategyName;
        
        const payload = {
            features: osmFeaturesCache,
            subdivisions: subPlots,
            frontage: Array.isArray(window.roadFrontage) ? window.roadFrontage : [],
            parcel_info: currentParcelData.parcel
        };
        
        showLoading(true, "Generating PDF Report...");
        $.ajax({
            url: API_BASE_URL + `/api/parcel/${plotNo}/generate_report?parcel_id=${parcelId}`,
            type: "POST",
            contentType: "application/json",
            data: JSON.stringify(payload),
            xhrFields: { responseType: 'blob' },
            success: function(blob) {
                showLoading(false);
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `Kurra_Report_${plotNo}.pdf`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                showToast("Report downloaded successfully", "success");
            },
            error: function() {
                showLoading(false);
                showToast("Failed to generate report", "error");
            }
        });
    });
}

// =============================================================================
// STEP 8: Load Village Sheet Extents & WMS Overlay
// =============================================================================
// Called when user selects a Sheet from the final dropdown.
// PROCESS:
//   1. Collect all 7 selected admin codes (district, subdiv, circle, mouza, survey, mapinst, sheet)
//   2. Build currentLevels string: "09,01,05,003,01,01,01,"
//   3. POST /proxy/MapInfo/getVVVVExtentGeoref to get sheet bounding box in GPS (srs=4326)
//   4. Detect anomalous extents (UTM values in GPS fields)  use Bihar fallback center
//   5. Fit the OpenLayers map view to the sheet bounding box (animated zoom)
//   6. Update WMS layer source parameter {gis_code} to load the cadastral tiles
//   7. Show plotLyr (cadastral WMS) and hide selPlotLyr (selected plot highlight)
//   8. Store currentGisCode for use in subsequent click/PNIU queries
// =============================================================================
function loadVillageSheet() {
    const dist = $("#select-district").val();
    const subdiv = $("#select-subdiv").val();
    const circle = $("#select-circle").val();
    const mouza = $("#select-mouza").val();
    const survey = $("#select-survey").val();
    const mapinst = $("#select-mapinst").val();
    const sheet = $("#select-sheet").val();

    // Construct level string (comma terminated)
    currentLevels = `${dist},${subdiv},${circle},${mouza},${survey},${mapinst},${sheet},`;
    
    showLoading(true, "Fetching georeferenced sheet boundaries...");

    $.post(API_BASE_URL + "/proxy/MapInfo/getVVVVExtentGeoref", {
        state: stateCode,
        gisLevels: currentLevels,
        srs: "4326" // Directly request GPS coordinates
    }, function(data) {
        showLoading(false);
        if (!data || !data.gisCode) {
            showToast("Failed to load sheet metadata", "error");
            return;
        }

        currentGisCode = data.gisCode;

        // Check for Sheet "00" or Database anomalies
        // Mismatched check: degrees (xmin/ymin) vs meters (xmax/ymax)
        // If xmax > 180, it's UTM meters, indicating corrupted extent
        const isAnomaly = (data.xmax > 180 && data.xmin < 180) || (data.xmin === 0 && data.ymin === 0);

        if (isAnomaly) {
            currentExtent = {
                xmin: 84.0, // General Bihar bounds fallback
                ymin: 24.5,
                xmax: 88.0,
                ymax: 27.5
            };
            showToast("Database boundary anomaly detected for Sheet 00. Centering on Bihar.", "warning");
            map.getView().setCenter([85.1376, 25.5941]);
            map.getView().setZoom(7);
        } else {
            currentExtent = {
                xmin: data.xmin,
                ymin: data.ymin,
                xmax: data.xmax,
                ymax: data.ymax
            };
            // Fit map view to GPS bounds
            map.getView().fit([data.xmin, data.ymin, data.xmax, data.ymax], {
                size: map.getSize(),
                duration: 1000
            });
        }

        // Update WMS layer source parameters
        plotLyr.getSource().updateParams({
            "gis_code": currentGisCode
        });
        plotLyr.setVisible(true);

        // Reset selected plot layer
        selPlotLyr.setVisible(false);
        clearDetails();
        
        showToast("Cadastral map overlaid successfully", "success");
    }, "json").fail(function() {
        showLoading(false);
        showToast("Error retrieving map boundaries", "error");
    });
}

// =============================================================================
// STEP 9: PNIU Search
// =============================================================================
// Searches for a plot by its 14-digit PNIU (Parcel Number Identification Unit) code.
// PNIU is a unique national plot identifier used in Indian land records.
// PROCESS:
//   1. Validate PNIU and currentGisCode are set
//   2. POST /proxy/MapInfo/getPointsfromPNIU with {state, pniu, gisCode}
//   3. Backend returns a raw comma-delimited string (non-JSON)
//   4. Parse the 6th comma-delimited field (index 5)  this is the plot number
//   5. Call selectPlotByNumber(plotNo) to load full parcel details
// =============================================================================
function executePniuSearch() {
    const pniu = $("#search-pniu").val().trim();
    if (!pniu) {
        showToast("Please enter a PNIU code", "warning");
        return;
    }
    if (!currentGisCode) {
        showToast("Please select the village sheet first", "warning");
        return;
    }

    showLoading(true, `Resolving PNIU code: ${pniu}...`);

    $.post(API_BASE_URL + "/proxy/MapInfo/getPointsfromPNIU", {
        state: stateCode,
        pniu: pniu,
        gisCode: currentGisCode
    }, function(data) {
        if (!data || data.includes("null") || data.split(",").length < 10) {
            showLoading(false);
            showToast("PNIU code not found in this village sheet", "error");
            return;
        }

        const parts = data.split(",");
        const plotNo = parts[5];
        
        // Query details and geometry for the resolved plot number
        selectPlotByNumber(plotNo);
    }, "text").fail(function() {
        showLoading(false);
        showToast("Failed to search PNIU code", "error");
    });
}

// Helper: Clear Details Sidebar
function clearDetails() {
    $("#val-plot-no").text("--");
    $("#val-khata-no").text("--");
    $("#val-pniu").text("--");
    $("#val-lat").text("--");
    $("#val-lon").text("--");
    $("#val-owners").text("--");
    
    $("#val-area-sqm").text("--");
    $("#val-area-acres").text("--");
    $("#val-area-hectares").text("--");
    $("#val-perimeter").text("--");
    $("#val-vertices-count").text("--");
    $("#val-longest-side").text("--");
    $("#val-shortest-side").text("--");
    $("#val-avg-side").text("--");
    
    $("#btn-view-ldm").prop("disabled", true);
    $("#btn-download-ldm").prop("disabled", true);
    $("#btn-export-geojson").prop("disabled", true);
    $("#btn-export-csv").prop("disabled", true);
    
    $("#kurra-results").hide();
    
    currentParcelData = null;
    if (vectorSource) {
        vectorSource.clear();
    }
    if (kurraVectorLayer) kurraVectorLayer.getSource().clear();
    if (osmVectorLayer) osmVectorLayer.getSource().clear();
}

// =============================================================================
// STEP 10: Fetch Full Plot Details and Geometry
// =============================================================================
// Called from: map singleclick handler, PNIU search, batch scraper
// PROCESS:
//   1. POST /proxy/MapInfo/getPlotDetailsAndInspection with {state, giscode, plot_no, levels}
//   2. Backend checks SQLite cache  returns immediately if found (fast path)
//   3. If not cached: backend fetches from 3 BhuNaksha endpoints, parses, persists to DB
//   4. Response: {success, parcel, vertices, segments, report}
//   5. Frontend calls displayParcelDetails(res) to update UI + map
// =============================================================================
function selectPlotByNumber(plotNo) {
    showLoading(true, `Loading details for plot ${plotNo}...`);
    
    $.post(API_BASE_URL + "/proxy/MapInfo/getPlotDetailsAndInspection", {
        state: stateCode,
        giscode: currentGisCode,
        plot_no: plotNo,
        levels: currentLevels
    }, function(res) {
        showLoading(false);
        if (res && res.success) {
            displayParcelDetails(res);
            zoomToParcel();
            showToast(`Plot ${plotNo} details loaded successfully`, "success");
        } else {
            showToast(res.error || "Failed to retrieve plot details", "error");
        }
    }, "json").fail(function(xhr) {
        showLoading(false);
        const err = xhr.responseJSON ? xhr.responseJSON.error : "Failed to query backend server";
        showToast(err, "error");
    });
}

// =============================================================================
// STEP 11: Display Parcel Details & Update Map
// =============================================================================
// Called by: selectPlotByNumber() after receiving plot data from backend.
// PROCESS:
//   A. Update sidebar fields:
//       Plot No, Khata No, PNIU, Lat, Lon, Owner Names
//       Area (sq m, acres, hectares)  uses official PDF area if available
//       Perimeter, vertex count, longest/shortest/average side lengths
//   B. Enable export buttons (GeoJSON, CSV) and subdivide button
//   C. Enable map drawing tools (tree, well placement)
//   D. Show/hide PDF report buttons based on whether PDF was downloaded
//   E. Call redrawSelectedVector()  draw the pink polygon on the map
//   F. Update selPlotLyr (WMS highlight layer) with plot_id to show government selection
//   G. Animate sidebar scroll to the Measurements section
//   H. Apply brief highlight animation (section-highlight CSS class)
// =============================================================================
function displayParcelDetails(data) {
    currentParcelData = data;
    
    // Update Details
    $("#val-plot-no").text(data.parcel.plot_no || "--");
    $("#val-khata-no").text(data.parcel.khata_no || "--");
    $("#val-pniu").text(data.parcel.pniu || "--");
    $("#val-lat").text(data.parcel.lat != null ? data.parcel.lat.toFixed(6) : "--");
    $("#val-lon").text(data.parcel.lon != null ? data.parcel.lon.toFixed(6) : "--");
    
    const $owners = $("#val-owners");
    $owners.empty();
    if (data.parcel.owner_names && data.parcel.owner_names.length > 0) {
        data.parcel.owner_names.forEach((name, i) => {
            $owners.append(`<div>${i+1}. ${name}</div>`);
        });
    } else {
        $owners.text("--");
    }
    
    // Update Measurements
    const officialArea = data.parcel.official_area_ha;
    const calcArea = data.parcel.area;
    
    if (officialArea != null) {
        $("#val-area-hectares").html(`<span style="color:#00e5ff; font-weight:bold;" title="Official Area from BhuNaksha LPM">${officialArea.toFixed(4)} ha (Official)</span>`);
        $("#val-area-acres").text(`${(officialArea * 2.47105).toFixed(3)} acres`);
        $("#val-area-sqm").text(`${(officialArea * 10000).toFixed(1)} m²`);
    } else {
        $("#val-area-sqm").text(calcArea != null ? `${calcArea.toFixed(1)} m² (Calc)` : "--");
        $("#val-area-acres").text(calcArea != null ? `${(calcArea / 4046.8564).toFixed(3)} acres` : "--");
        $("#val-area-hectares").text(calcArea != null ? `${(calcArea / 10000.0).toFixed(4)} ha` : "--");
    }
    
    $("#val-perimeter").text(data.parcel.perimeter != null ? `${data.parcel.perimeter.toFixed(1)} m` : "--");
    $("#val-vertices-count").text(data.vertices.length || "--");
    
    if (data.segments && data.segments.length > 0) {
        const lengths = data.segments.map(s => s.length_meters);
        const maxLen = Math.max(...lengths);
        const minLen = Math.min(...lengths);
        const sumLen = lengths.reduce((a, b) => a + b, 0);
        const avgLen = sumLen / lengths.length;
        
        $("#val-longest-side").text(`${maxLen.toFixed(1)} m`);
        $("#val-shortest-side").text(`${minLen.toFixed(1)} m`);
        $("#val-avg-side").text(`${avgLen.toFixed(1)} m`);
    } else {
        $("#val-longest-side").text("--");
        $("#val-shortest-side").text("--");
        $("#val-avg-side").text("--");
    }
    
    // Enable Exports & Kurra
    $("#btn-export-geojson").prop("disabled", false);
    $("#btn-export-csv").prop("disabled", false);
    $("#btn-subdivide").prop("disabled", false);
    
    // Enable Manual Map Overrides
    $("#btn-add-tree").prop("disabled", false);
    $("#btn-add-well").prop("disabled", false);
    $("#btn-draw-road").prop("disabled", false);
    $("#btn-delete-obj").prop("disabled", false);
    
    // Enable PDF controls
    if (data.report && data.report.url) {
        $("#btn-view-ldm").prop("disabled", false);
        $("#btn-download-ldm").prop("disabled", false);
    } else {
        $("#btn-view-ldm").prop("disabled", true);
        $("#btn-download-ldm").prop("disabled", true);
    }
    
    // Draw vector polygon on map
    redrawSelectedVector();
    
    // Highlight WMS layer
    selPlotLyr.getSource().updateParams({
        "gis_code": currentGisCode,
        "plot_id": data.parcel.plot_id
    });
    selPlotLyr.setVisible(true);

    // Auto-scroll sidebar content to the Measurements section
    const $sidebarContent = $(".sidebar-content");
    const $measurementSection = $("#measurement-section");
    if ($sidebarContent.length && $measurementSection.length) {
        const sidebarOffset = $sidebarContent.offset();
        const measurementOffset = $measurementSection.offset();
        if (sidebarOffset && measurementOffset) {
            const scrollTopTarget = measurementOffset.top - sidebarOffset.top + $sidebarContent.scrollTop() - 10;
            $sidebarContent.animate({
                scrollTop: scrollTopTarget
            }, 600);
        }
    }

    // Trigger visual highlight animation
    const $measurementCard = $("#measurement-card");
    if ($measurementCard.length) {
        $measurementCard.addClass("section-highlight");
        setTimeout(() => {
            $measurementCard.removeClass("section-highlight");
        }, 2000);
    }
}


// =============================================================================
// STEP 12: Draw/Refresh Vector Polygon on Map
// =============================================================================
// Clears the vectorSource and redraws the selected parcel polygon using
// the GPS coordinates from currentParcelData.vertices.
//
// POLYGON RENDERING:
//   - OpenLayers requires polygons in the map projection (EPSG:4326 here)
//   - Polygon must be closed (first point == last point)
//   - Coordinates are taken directly from vertex GPS data (lon, lat)
//
// STYLE (vectorStyleFunction at top of file):
//   - Pink stroke (#ff3366, 3px), semi-transparent fill
//   - White circles at each vertex
//   - Side length labels at midpoints of each segment
//   - River-adjacent segments highlighted in cyan
// =============================================================================
function redrawSelectedVector() {
    if (!vectorSource) return;
    vectorSource.clear();
    if (!currentParcelData || !currentParcelData.vertices || currentParcelData.vertices.length === 0) return;
    
    // Transform coordinates adding current user georef offset shift
    const coords = currentParcelData.vertices.map(v => [
        v.lon,
        v.lat
    ]);
    
    // Polygon must be closed (start == end) in OpenLayers
    if (coords.length > 0 && (coords[0][0] !== coords[coords.length-1][0] || coords[0][1] !== coords[coords.length-1][1])) {
        coords.push(coords[0]);
    }
    
    const polyGeom = new ol.geom.Polygon([coords]);
    const feature = new ol.Feature({
        geometry: polyGeom
    });
    
    feature.set('segment_lengths', currentParcelData.segments.map(s => s.length_meters));
    vectorSource.addFeature(feature);
}

// =============================================================================
// STEP 13: Zoom Map to Selected Parcel
// =============================================================================
// Fits the OpenLayers view to the bounding box of the selected parcel's vertices.
// Uses a 1-second animated pan/zoom transition.
// Called after displayParcelDetails() to center the map on the selected plot.
// =============================================================================
function zoomToParcel() {
    if (!currentParcelData || !currentParcelData.vertices || currentParcelData.vertices.length === 0) return;
    
    const lons = currentParcelData.vertices.map(v => v.lon);
    const lats = currentParcelData.vertices.map(v => v.lat);
    
    const xmin = Math.min(...lons);
    const xmax = Math.max(...lons);
    const ymin = Math.min(...lats);
    const ymax = Math.max(...lats);
    
    map.getView().fit([xmin, ymin, xmax, ymax], {
        size: map.getSize(),
        duration: 1000
    });
}

// Helper: Reset dropdowns below a certain level
function resetDropdownsFrom(levelNumber) {
    for (let l = levelNumber; l <= 7; l++) {
        const selectId = getSelectIdForLevel(l);
        const $select = $(`#${selectId}`);
        $select.empty().append(`<option value="">--Select ${getLevelLabel(l)}--</option>`);
        $select.prop("disabled", true);
    }
}

// Helper: Map Level to Select ID
function getSelectIdForLevel(level) {
    const ids = {
        1: "select-district",
        2: "select-subdiv",
        3: "select-circle",
        4: "select-mouza",
        5: "select-survey",
        6: "select-mapinst",
        7: "select-sheet"
    };
    return ids[level];
}

// Helper: Map Level to human-readable labels
function getLevelLabel(level) {
    const labels = {
        1: "District",
        2: "Subdivision",
        3: "Circle",
        4: "Mouza",
        5: "Survey Type",
        6: "Map Instance",
        7: "Sheet"
    };
    return labels[level];
}

// Helper: Toggle Loading Overlay
function showLoading(show, text = "Loading...") {
    const $loader = $("#loading");
    $("#loading-text").text(text);
    if (show) {
        $loader.addClass("active");
    } else {
        $loader.removeClass("active");
    }
}

// Helper: Toast Notifications
let toastTimeout;
function showToast(message, type = "info") {
    clearTimeout(toastTimeout);
    const $toast = $("#toast");
    const $icon = $("#toast-icon");
    $("#toast-message").text(message);

    // Reset styles
    $icon.removeClass("warning error success info");
    
    if (type === "warning") $icon.addClass("fa-exclamation-triangle warning");
    else if (type === "error") $icon.addClass("fa-times-circle error");
    else if (type === "success") $icon.addClass("fa-check-circle success");
    else $icon.addClass("fa-info-circle info");

    $toast.addClass("active");
    
    toastTimeout = setTimeout(() => {
        $toast.removeClass("active");
    }, 4000);
}
