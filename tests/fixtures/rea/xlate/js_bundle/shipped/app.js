(()=>{function c(r){let e=2166136261;for(let t=0;t<r.length;t++)e^=r.charCodeAt(t),e=Math.imul(e,16777619)>>>0;return e}function n(r){switch(r){case 4:return 40;case 5:return 50;case 6:return 60;default:return-1}}function u(r){let e=r||"/api/icdev/fixture/route";return e.length>64?null:e+" "+c(e)+" "+n(e.length%7)}console.log(u(process.argv[2]));})();
//# sourceMappingURL=app.js.map
