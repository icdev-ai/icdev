/* ICDEV Slide Deck — Three.js Scene Renderer
 * Requires Three.js r134 UMD (window.THREE) loaded before this script.
 * Exposes: window.ICDEVThreeRenderer
 */
(function (global) {
  'use strict';

  var ICDEVThreeRenderer = {

    isWebGLAvailable: function () {
      try {
        var canvas = document.createElement('canvas');
        return !!(window.WebGLRenderingContext &&
          (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')));
      } catch (e) {
        return false;
      }
    },

    /* init(canvas, sceneConfig) → { stop() } | null */
    init: function (canvas, sceneConfig) {
      if (!window.THREE) {
        console.warn('ICDEVThreeRenderer: THREE not loaded');
        return null;
      }
      if (!this.isWebGLAvailable()) {
        this._showFallback(canvas, 'WebGL not available in this browser.');
        return null;
      }

      var cfg = sceneConfig || {};
      var objects = cfg.objects || [];
      var lights = cfg.lights || [
        { type: 'ambient', color: '#ffffff', intensity: 0.4 },
        { type: 'directional', color: '#c8a951', intensity: 0.8, position: [5, 10, 5] }
      ];
      var camCfg = cfg.camera || { type: 'perspective', fov: 60, position: [0, 4, 18] };
      var bgColor = cfg.background || '#0a1628';

      // Scene
      var scene = new THREE.Scene();
      scene.background = new THREE.Color(bgColor);

      // Camera
      var w = canvas.clientWidth || canvas.width || 800;
      var h = canvas.clientHeight || canvas.height || 450;
      var camera = new THREE.PerspectiveCamera(camCfg.fov || 60, w / h, 0.1, 1000);
      var cp = camCfg.position || [0, 4, 18];
      camera.position.set(cp[0], cp[1], cp[2]);

      // Renderer
      var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(w, h);

      // Lights
      lights.forEach(function (lc) {
        var light;
        if (lc.type === 'ambient') {
          light = new THREE.AmbientLight(lc.color || '#ffffff', lc.intensity || 0.4);
        } else if (lc.type === 'directional') {
          light = new THREE.DirectionalLight(lc.color || '#ffffff', lc.intensity || 0.8);
          var lp = lc.position || [5, 10, 5];
          light.position.set(lp[0], lp[1], lp[2]);
        } else if (lc.type === 'point') {
          light = new THREE.PointLight(lc.color || '#ffffff', lc.intensity || 1.0, lc.distance || 100);
          var pp = lc.position || [0, 5, 0];
          light.position.set(pp[0], pp[1], pp[2]);
        }
        if (light) scene.add(light);
      });

      // Object map for line resolution
      var objMap = {};
      var meshes = []; // {mesh, anim, baseY, baseAngle}
      var textLabels = []; // DOM divs for cleanup
      var container = canvas.parentElement;

      // Pass 1: non-line objects
      objects.forEach(function (oc) {
        if (oc.type === 'line') return;

        var geo, mat, mesh;
        var color = new THREE.Color(oc.color || '#4a90d9');
        var scale = oc.scale || 1.0;
        var pos = oc.position || [0, 0, 0];

        if (oc.type === 'sphere') {
          geo = new THREE.SphereGeometry(0.6 * scale, 32, 32);
          mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.3, metalness: 0.2 });
        } else if (oc.type === 'box') {
          var s = oc.size || [1.2, 1.2, 1.2];
          geo = new THREE.BoxGeometry(s[0] * scale, s[1] * scale, s[2] * scale);
          mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.4, metalness: 0.1 });
        } else if (oc.type === 'cylinder') {
          geo = new THREE.CylinderGeometry(0.5 * scale, 0.5 * scale, 1.5 * scale, 32);
          mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.4, metalness: 0.1 });
        } else if (oc.type === 'torus') {
          geo = new THREE.TorusGeometry(0.8 * scale, 0.2 * scale, 16, 100);
          mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.3, metalness: 0.3 });
        } else if (oc.type === 'points') {
          var pts = [];
          var count = oc.count || 80;
          var spread = oc.spread || 3;
          for (var pi = 0; pi < count; pi++) {
            pts.push(
              (Math.random() - 0.5) * spread * 2,
              (Math.random() - 0.5) * spread * 2,
              (Math.random() - 0.5) * spread * 2
            );
          }
          geo = new THREE.BufferGeometry();
          geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
          mat = new THREE.PointsMaterial({ color: color, size: 0.08 });
          mesh = new THREE.Points(geo, mat);
          mesh.position.set(pos[0], pos[1], pos[2]);
          scene.add(mesh);
          objMap[oc.id] = mesh;
          meshes.push({ mesh: mesh, anim: oc.animation || { type: 'none' }, baseY: pos[1], elapsed: 0 });
          return;
        } else if (oc.type === 'text_label') {
          // CSS overlay div instead of TextGeometry
          if (container) {
            var div = document.createElement('div');
            div.style.cssText = [
              'position:absolute',
              'pointer-events:none',
              'color:' + (oc.color || '#ffffff'),
              'font-size:' + (oc.fontSize || 14) + 'px',
              'font-family:system-ui,sans-serif',
              'font-weight:600',
              'text-shadow:0 1px 3px rgba(0,0,0,0.8)',
              'white-space:nowrap',
            ].join(';');
            div.textContent = oc.label || oc.id;
            div._threePos = new THREE.Vector3(pos[0], pos[1], pos[2]);
            container.style.position = 'relative';
            container.appendChild(div);
            textLabels.push(div);
          }
          return;
        } else {
          // default sphere for unknown types
          geo = new THREE.SphereGeometry(0.5 * scale, 16, 16);
          mat = new THREE.MeshStandardMaterial({ color: color });
        }

        if (!mesh) {
          mesh = new THREE.Mesh(geo, mat);
        }
        mesh.position.set(pos[0], pos[1], pos[2]);
        scene.add(mesh);
        if (oc.id) objMap[oc.id] = mesh;
        meshes.push({ mesh: mesh, anim: oc.animation || { type: 'none' }, baseY: pos[1], elapsed: 0 });
      });

      // Pass 2: line objects (need objMap populated)
      objects.forEach(function (oc) {
        if (oc.type !== 'line') return;
        var fromMesh = objMap[oc.from];
        var toMesh = objMap[oc.to];
        if (!fromMesh || !toMesh) return;

        var points = [fromMesh.position.clone(), toMesh.position.clone()];
        var geo = new THREE.BufferGeometry().setFromPoints(points);
        var mat = new THREE.LineBasicMaterial({
          color: new THREE.Color((oc.color || '#c8a951').slice(0, 7)),
          opacity: oc.opacity || 0.5,
          transparent: true,
        });
        var line = new THREE.Line(geo, mat);
        scene.add(line);
        if (oc.id) objMap[oc.id] = line;
      });

      // Clock for delta-time animations
      var clock = new THREE.Clock();
      var animId;
      var stopped = false;

      function updateTextLabels() {
        if (!textLabels.length) return;
        var w2 = canvas.clientWidth, h2 = canvas.clientHeight;
        textLabels.forEach(function (div) {
          var v = div._threePos.clone().project(camera);
          var x = (v.x * 0.5 + 0.5) * w2;
          var y = (-v.y * 0.5 + 0.5) * h2;
          div.style.left = x + 'px';
          div.style.top = y + 'px';
          div.style.transform = 'translate(-50%,-50%)';
        });
      }

      function animate() {
        if (stopped) return;
        animId = requestAnimationFrame(animate);
        var delta = clock.getDelta();

        meshes.forEach(function (item) {
          var mesh = item.mesh;
          var anim = item.anim;
          item.elapsed = (item.elapsed || 0) + delta;
          var t = item.elapsed;
          var speed = anim.speed || 1.0;

          if (anim.type === 'rotate') {
            var axis = anim.axis || 'y';
            mesh.rotation[axis] += speed * delta;
          } else if (anim.type === 'float') {
            var amp = anim.amplitude || 0.4;
            mesh.position.y = item.baseY + Math.sin(t * speed) * amp;
          } else if (anim.type === 'pulse') {
            var s = 1 + Math.sin(t * speed) * 0.1;
            mesh.scale.setScalar(s);
          } else if (anim.type === 'orbit') {
            var radius = anim.radius || 3;
            mesh.position.x = Math.cos(t * speed) * radius;
            mesh.position.z = Math.sin(t * speed) * radius;
          }
        });

        updateTextLabels();
        renderer.render(scene, camera);
      }

      // Resize observer
      var resizeObs = null;
      if (window.ResizeObserver) {
        resizeObs = new ResizeObserver(function () {
          var nw = canvas.clientWidth, nh = canvas.clientHeight;
          if (!nw || !nh) return;
          camera.aspect = nw / nh;
          camera.updateProjectionMatrix();
          renderer.setSize(nw, nh);
        });
        resizeObs.observe(canvas);
      }

      animate();

      return {
        stop: function () {
          stopped = true;
          if (animId) cancelAnimationFrame(animId);
          if (resizeObs) resizeObs.disconnect();
          textLabels.forEach(function (div) {
            if (div.parentNode) div.parentNode.removeChild(div);
          });
          renderer.dispose();
        }
      };
    },

    _showFallback: function (canvas, msg) {
      canvas.style.display = 'none';
      var div = document.createElement('div');
      div.style.cssText = 'padding:24px;color:#c8d2dc;font-style:italic;font-size:14px;';
      div.textContent = msg || '3D animation requires WebGL.';
      if (canvas.parentNode) canvas.parentNode.insertBefore(div, canvas);
    }
  };

  global.ICDEVThreeRenderer = ICDEVThreeRenderer;
}(window));
