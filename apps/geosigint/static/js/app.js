/* GeoSIGINT — Main JavaScript */
// Currently, page-specific JS is in template script blocks.
// This file is reserved for shared utilities.

window.GeoSIGINT = window.GeoSIGINT || {};

GeoSIGINT.formatFreq = function(hz) {
    if (hz >= 1e9) return (hz/1e9).toFixed(3) + ' GHz';
    if (hz >= 1e6) return (hz/1e6).toFixed(3) + ' MHz';
    return (hz/1e3).toFixed(1) + ' kHz';
};

GeoSIGINT.formatTime = function(iso) {
    if (!iso) return '—';
    return iso.replace('T', ' ').slice(0, 19);
};
