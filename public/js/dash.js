/**
 * Geospatial Dashboard Frontend
 * Connects to the /geospatial SocketIO namespace, renders Leaflet markers,
 * and updates them upon receiving aggregated GeoJSON.
 */
(function () {
    'use strict';

    // Map configuration — Eastern Mediterranean / Persian Gulf
    var MAP_CENTER = [30.0, 45.0];
    var MAP_ZOOM = 5;

    var map = L.map('map', {
        center: MAP_CENTER,
        zoom: MAP_ZOOM,
        zoomControl: true,
        attributionControl: true,
    });

    // Dark-themed tile layer (CartoDB Dark Matter)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19,
    }).addTo(map);

    var _markerLayer = L.layerGroup().addTo(map);
    var _wsConnected = false;

    function updateStatus(connected) {
        _wsConnected = connected;
        var dot = document.getElementById('ws-status-dot');
        var text = document.getElementById('ws-status-text');
        if (dot) dot.className = 'status-dot' + (connected ? ' online' : '');
        if (text) text.textContent = connected ? 'WebSocket connected' : 'WebSocket disconnected';
    }

    function updateMarkerCount(count) {
        var el = document.getElementById('marker-count');
        if (el) el.textContent = count + ' marker' + (count === 1 ? '' : 's');
    }

    function clearMarkers() {
        _markerLayer.clearLayers();
    }

    function renderGeoJSON(geojson) {
        if (!geojson || !geojson.features) return;
        clearMarkers();

        var count = 0;
        geojson.features.forEach(function (feature) {
            var geom = feature.geometry;
            var props = feature.properties || {};
            if (!geom || geom.type !== 'Point') return;

            var coords = geom.coordinates;
            var lat = coords[1];
            var lon = coords[0];
            if (lat == null || lon == null) return;

            var color = '#4a90d9';
            if (props.source === 'osint') color = '#22c55e';
            else if (props.source === 'satellite') color = '#f59e0b';
            else if (props.source === 'diplomatic') color = '#a855f7';

            var marker = L.circleMarker([lat, lon], {
                radius: 8,
                fillColor: color,
                color: '#fff',
                weight: 1,
                opacity: 1,
                fillOpacity: 0.85,
            });

            var popupHtml = '<div style="font-size:13px; line-height:1.5;">' +
                '<strong style="font-size:14px;">' + escapeHtml(props.title || 'Untitled') + '</strong><br>' +
                '<span style="color:#94a3b8;">Source:</span> ' + escapeHtml(props.source || 'unknown') + '<br>' +
                '<span style="color:#94a3b8;">ID:</span> ' + escapeHtml(props.id || '-') + '<br>' +
                '<span style="color:#94a3b8;">Time:</span> ' + escapeHtml(props.timestamp || '-') + '<br>' +
                '<p style="margin:6px 0 0; color:#cbd5e1;">' + escapeHtml(props.content || '') + '</p>' +
                '</div>';

            marker.bindPopup(popupHtml);
            marker.addTo(_markerLayer);
            count++;
        });

        updateMarkerCount(count);

        // Auto-fit bounds when we have markers, but respect a max zoom so we don't zoom too far
        if (count > 0) {
            var bounds = _markerLayer.getBounds();
            if (bounds.isValid()) {
                map.fitBounds(bounds, { padding: [40, 40], maxZoom: 10 });
            }
        }
    }

    function escapeHtml(text) {
        if (text == null) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ------------------------------------------------------------------
    // SocketIO connection
    // ------------------------------------------------------------------
    var socket = io({
        path: '/socket.io',
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionDelay: 2000,
    });

    socket.on('connect', function () {
        updateStatus(true);
        // Join the geospatial namespace room
        socket.emit('aggregate_request', {});
    });

    socket.on('disconnect', function () {
        updateStatus(false);
    });

    socket.on('connected', function (data) {
        console.log('SocketIO connected:', data);
    });

    socket.on('aggregate_update', function (data) {
        console.log('Received aggregate_update:', data);
        renderGeoJSON(data);
    });

    socket.on('aggregate_error', function (data) {
        console.error('Aggregate error:', data);
    });

    // ------------------------------------------------------------------
    // Global functions wired to HTML buttons
    // ------------------------------------------------------------------
    window.triggerAggregate = function () {
        socket.emit('aggregate_request', {});
    };

    window.loadDemoData = function () {
        fetch('/api/geospatial/aggregate')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                renderGeoJSON(data);
            })
            .catch(function (e) {
                console.error('Failed to load demo data:', e);
            });
    };

    // Load initial data on page load (HTTP fallback if WebSocket is slow)
    document.addEventListener('DOMContentLoaded', function () {
        window.loadDemoData();
    });
})();
