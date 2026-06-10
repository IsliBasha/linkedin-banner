(function () {
    if (window.__bannerCropPatchInstalled) return;
    window.__bannerCropPatchInstalled = true;

    // Clamp and clean a single coordinate value:
    //  - values within 1e-9 of 0 → exact 0
    //  - values within 1e-9 of 1 → exact 1
    //  - all others → clamped to [0, 1]
    // LinkedIn's crop editor generates values like 9.8e-16 (should be 0) and
    // 1.000000000000001 (should be 1) due to rotation-matrix floating-point drift.
    // Servers often require exact 0.0 / 1.0 at the corners of a full-image crop.
    function cleanCoord(v) {
        var n = Number(v);
        if (Math.abs(n) < 1e-9)     return 0;
        if (Math.abs(n - 1) < 1e-9) return 1;
        return Math.max(0, Math.min(1, n));
    }

    function clampStates(states) {
        if (!Array.isArray(states)) return false;
        var fixed = false;
        states.forEach(function (state) {
            var val = state && state.value;
            if (!val || typeof val !== 'object' || !val.mediaFiles) return;
            (val.mediaFiles || []).forEach(function (mf) {
                var cr = mf && mf.editData && mf.editData.croppedRegion;
                if (!cr || typeof cr !== 'object') return;
                ['topLeft','topRight','bottomLeft','bottomRight'].forEach(function (corner) {
                    var pt = cr[corner];
                    if (!pt) return;
                    ['x','y'].forEach(function (ax) {
                        var v = pt[ax];
                        if (v !== undefined && v !== null) {
                            var c = cleanCoord(v);
                            if (c !== v) { pt[ax] = c; fixed = true; }
                        }
                    });
                });
            });
        });
        return fixed;
    }

    var origFetch = window.fetch;
    window.fetch = function (url, options) {
        var rest = Array.prototype.slice.call(arguments, 2);
        // Only intercept the actual save endpoint (match by URL, not body content).
        // profileImageRegister body also contains 'saveProfileBackgroundImage' as a
        // next-action reference, so body-content matching would over-fire.
        var isSave = typeof url === 'string' &&
            url.indexOf('saveProfileBackgroundImage') !== -1;
        if (isSave && options && options.body && typeof options.body === 'string') {
            try {
                var body = JSON.parse(options.body);
                var f1 = clampStates(body.states || []);
                var f2 = clampStates(((body.requestedArguments || {}).states) || []);
                if (f1 || f2) {
                    console.log('[BannerPatch] clamped crop coords → ' + url);
                    options = Object.assign({}, options, {body: JSON.stringify(body)});
                } else {
                    console.log('[BannerPatch] save endpoint — coords already clean');
                }
            } catch (e) {
                console.error('[BannerPatch] parse error:', e);
            }
        }
        return origFetch.apply(this, [url, options].concat(rest));
    };
    console.log('[BannerPatch] fetch interceptor installed');
})();
