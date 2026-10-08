/* SIGNIX — production rooftop 3D visualizer.
 *
 * Design goals:
 *  - the user's drawn footprint is authoritative;
 *  - AI roof planes/structures are visual guidance, not silent financial changes;
 *  - PV modules use physical dimensions and polygon collision tests;
 *  - the scene reads like a solar-design tool: roof planes, structures, compass,
 *    shadows, orientation, and useful metrics rather than a generic 3D house.
 */
(function () {
  var scene, camera, renderer, rootGroup, animationId;
  var currentPitch = 0, currentPanels = 0, roofType = 'flat';
  var aiStructuresApplied = false;
  var localRing = null, localObstructions = [], aiObstructions = [], aiPlanes = [];
  var footprintSpan = 12, roofInfo = null;
  var cameraDistance = 14, cameraAzimuth = 0.72, cameraElevation = 0.62;
  var userInteracted = false, aiAnalysis = null, satelliteTexture = null, siteRoofModel = null, siteModelLoading = false;
  var siteSegmentPlanes = [];
  var shadeGrid = null, shadeSide = null, shadeAvoidedPanels = 0;
  var currentOrientationDeg = (typeof ORIENTATION_DEG !== 'undefined' && Number.isFinite(Number(ORIENTATION_DEG))) ? Number(ORIENTATION_DEG) : 180;
  var savedOrientationDeg = currentOrientationDeg;
  var savedAnnualGeneration = (typeof ANNUAL_GENERATION !== 'undefined') ? Number(ANNUAL_GENERATION || 0) : 0;
  var orientationPreviewTimer = null, orientationRequestSeq = 0, orientationDirty = false;

  var PANEL_W = 1.134, PANEL_D = 1.762, PANEL_GAP = 0.07;
  var EDGE_SETBACK = 0.32, PANEL_THICKNESS = 0.055, FLAT_PANEL_TILT = 10 * Math.PI / 180;

  function hasWebGL() {
    try { var c = document.createElement('canvas'); return !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl'))); }
    catch (e) { return false; }
  }
  function extractRing(g) {
    if (!g || !g.coordinates) return null;
    var r = g.coordinates[0];
    if (!r || r.length < 3) { if (g.coordinates.length >= 3 && typeof g.coordinates[0][0] === 'number') r = g.coordinates; else return null; }
    return r;
  }
  function geoJsonRingToLocal(ring) {
    if (!ring || ring.length < 3) return null;
    var pts=[];
    ring.forEach(function(p){
      if (!p || p.length<2) return;
      var lng=Number(p[0]),lat=Number(p[1]); if(!isFinite(lng)||!isFinite(lat))return;
      if(lat>=-90&&lat<=90&&lng>=-180&&lng<=180)pts.push({lat:lat,lng:lng});
    });
    if(pts.length<3)return null;
    var cLat=0,cLng=0; for(var i=0;i<pts.length;i++){cLat+=pts[i].lat;cLng+=pts[i].lng;} cLat/=pts.length;cLng/=pts.length;
    var mLat=111320,mLng=111320*Math.cos(cLat*Math.PI/180);
    return pts.map(function(p){return[(p.lng-cLng)*mLng,-(p.lat-cLat)*mLat];});
  }
  function ringBounds(r){var minX=Infinity,maxX=-Infinity,minZ=Infinity,maxZ=-Infinity;r.forEach(function(p){minX=Math.min(minX,p[0]);maxX=Math.max(maxX,p[0]);minZ=Math.min(minZ,p[1]);maxZ=Math.max(maxZ,p[1]);});return{minX:minX,maxX:maxX,minZ:minZ,maxZ:maxZ,width:maxX-minX,depth:maxZ-minZ,cx:(minX+maxX)/2,cz:(minZ+maxZ)/2};}
  function polygonCentroid(r){var x=0,z=0,a=0;for(var i=0,j=r.length-1;i<r.length;j=i++){var c=r[j][0]*r[i][1]-r[i][0]*r[j][1];a+=c;x+=(r[j][0]+r[i][0])*c;z+=(r[j][1]+r[i][1])*c;}return Math.abs(a)<1e-8?[r[0][0],r[0][1]]:[x/(3*a),z/(3*a)];}
  function principalAngle(r){var c=polygonCentroid(r),xx=0,zz=0,xz=0;r.forEach(function(p){var dx=p[0]-c[0],dz=p[1]-c[1];xx+=dx*dx;zz+=dz*dz;xz+=dx*dz;});return .5*Math.atan2(2*xz,xx-zz);}
  function rotatePoint(p,a){var c=Math.cos(a),s=Math.sin(a);return[p[0]*c-p[1]*s,p[0]*s+p[1]*c];}
  function transformRing(r,a){return r.map(function(p){return rotatePoint(p,a);});}
  function pointInRing(x,z,r){var inside=false;for(var i=0,j=r.length-1;i<r.length;j=i++){var xi=r[i][0],zi=r[i][1],xj=r[j][0],zj=r[j][1];if((zi>z)!=(zj>z)&&x<((xj-xi)*(z-zi))/((zj-zi)||1e-12)+xi)inside=!inside;}return inside;}
  function orientation(a,b,c){var v=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);return Math.abs(v)<1e-8?0:(v>0?1:2);}
  function onSegment(a,b,p){return Math.min(a[0],b[0])-1e-7<=p[0]&&p[0]<=Math.max(a[0],b[0])+1e-7&&Math.min(a[1],b[1])-1e-7<=p[1]&&p[1]<=Math.max(a[1],b[1])+1e-7;}
  function segmentsIntersect(a,b,c,d){var o1=orientation(a,b,c),o2=orientation(a,b,d),o3=orientation(c,d,a),o4=orientation(c,d,b);if(o1!==o2&&o3!==o4)return true;return(o1===0&&onSegment(a,b,c))||(o2===0&&onSegment(a,b,d))||(o3===0&&onSegment(c,d,a))||(o4===0&&onSegment(c,d,b));}
  function polygonEdgesIntersect(a,b){for(var i=0;i<a.length;i++)for(var j=0;j<b.length;j++)if(segmentsIntersect(a[i],a[(i+1)%a.length],b[j],b[(j+1)%b.length]))return true;return false;}
  function rect(cx,cz,w,d,a){var hw=w/2,hd=d/2,c=Math.cos(a),s=Math.sin(a);return[[-hw,-hd],[hw,-hd],[hw,hd],[-hw,hd]].map(function(p){return[cx+p[0]*c-p[1]*s,cz+p[0]*s+p[1]*c];});}
  function shadeValueAt(x,z){
    var g=shadeGrid; if(!g||!g.x||!g.z||!g.relativeFlux||!g.rows||!g.cols)return null;
    function bracket(arr,v){var asc=arr[arr.length-1]>=arr[0];var lo=0,hi=arr.length-1;while(lo<hi){var mid=Math.floor((lo+hi)/2);if((asc?arr[mid]:-arr[mid])<(asc?v:-v))lo=mid+1;else hi=mid;}return Math.max(1,Math.min(arr.length-1,lo));}
    var xi=bracket(g.x,x),zi=bracket(g.z,z),x0=g.x[xi-1],x1=g.x[xi],z0=g.z[zi-1],z1=g.z[zi];
    var tx=x1!==x0?(x-x0)/(x1-x0):0,tz=z1!==z0?(z-z0)/(z1-z0):0;
    function v(r,c){var n=g.relativeFlux[r*g.cols+c];return Number.isFinite(Number(n))?Number(n):null;}
    var a=v(zi-1,xi-1),b=v(zi-1,xi),c=v(zi,xi-1),d=v(zi,xi);
    if([a,b,c,d].some(function(v){return v===null;}))return null;
    return a*(1-tx)*(1-tz)+b*tx*(1-tz)+c*(1-tx)*tz+d*tx*tz;
  }
  function panelTouchesShade(poly){
    if(!shadeGrid)return false;
    var pts=[[polygonCentroid(poly)[0],polygonCentroid(poly)[1]]].concat(poly);
    var threshold=Number(shadeGrid.threshold)||0.65;
    return pts.some(function(pt){var v=shadeValueAt(pt[0],pt[1]);return v!==null&&v<threshold;});
  }
  function polygonFits(poly,roof,obstructions){if(!roof)return false;for(var i=0;i<poly.length;i++)if(!pointInRing(poly[i][0],poly[i][1],roof))return false;if(polygonEdgesIntersect(poly,roof))return false;for(i=0;i<obstructions.length;i++){var o=obstructions[i];if(pointInRing(poly[0][0],poly[0][1],o)||polygonEdgesIntersect(poly,o))return false;var oc=polygonCentroid(o);if(pointInRing(oc[0],oc[1],poly))return false;}if(panelTouchesShade(poly)){shadeAvoidedPanels++;return false;}return true;}
  function clearGroup(g){if(!g)return;while(g.children.length){var c=g.children.pop();if(c.geometry)c.geometry.dispose();if(c.material){if(Array.isArray(c.material))c.material.forEach(function(m){m.dispose();});else c.material.dispose();}}}
  function shapeFromRing(r){var s=new THREE.Shape();s.moveTo(r[0][0],r[0][1]);for(var i=1;i<r.length;i++)s.lineTo(r[i][0],r[i][1]);s.closePath();return s;}

  function siteHeightAt(x, z) {
    var g = siteRoofModel && siteRoofModel.dsmGrid;
    if (!g || !g.x || !g.z || !g.heights || !g.rows || !g.cols) return null;
    var xs=g.x, zs=g.z, rows=g.rows, cols=g.cols;
    if (x<Math.min(xs[0],xs[cols-1]) || x>Math.max(xs[0],xs[cols-1]) || z<Math.min(zs[0],zs[rows-1]) || z>Math.max(zs[0],zs[rows-1])) return null;
    function locate(arr,v){
      var asc=arr[arr.length-1]>=arr[0], lo=0, hi=arr.length-1;
      if(!asc){ v=-v; arr=arr.map(function(n){return -n;}); }
      while(lo<hi){var mid=Math.floor((lo+hi)/2); if(arr[mid]<v)lo=mid+1;else hi=mid;}
      var i=Math.max(1,Math.min(arr.length-1,lo)); return [i-1,i];
    }
    var xi=locate(xs,x), zi=locate(zs,z), x0=xs[xi[0]], x1=xs[xi[1]], z0=zs[zi[0]], z1=zs[zi[1]];
    var tx=(x1-x0)!==0?(x-x0)/(x1-x0):0, tz=(z1-z0)!==0?(z-z0)/(z1-z0):0;
    function val(r,c){var v=g.heights[r*cols+c]; return Number.isFinite(Number(v))?Number(v):null;}
    var a=val(zi[0],xi[0]), b=val(zi[0],xi[1]), c=val(zi[1],xi[0]), d=val(zi[1],xi[1]);
    if([a,b,c,d].some(function(v){return v===null;})) return null;
    return a*(1-tx)*(1-tz)+b*tx*(1-tz)+c*(1-tx)*tz+d*tx*tz;
  }
  function buildSiteRoofMesh(){
    var g=siteRoofModel && siteRoofModel.dsmGrid; if(!g)return null;
    var positions=[], normals=[]; var rows=g.rows, cols=g.cols, xs=g.x, zs=g.z, h=g.heights;
    function valid(r,c){var v=h[r*cols+c];return Number.isFinite(Number(v));}
    for(var r=0;r<rows-1;r++) for(var c=0;c<cols-1;c++){
      if(!valid(r,c)||!valid(r,c+1)||!valid(r+1,c)||!valid(r+1,c+1)) continue;
      var p00=[xs[c],Number(h[r*cols+c]),zs[r]], p10=[xs[c+1],Number(h[r*cols+c+1]),zs[r]], p01=[xs[c],Number(h[(r+1)*cols+c]),zs[r+1]], p11=[xs[c+1],Number(h[(r+1)*cols+c+1]),zs[r+1]];
      var cx=(p00[0]+p10[0]+p01[0]+p11[0])/4, cz=(p00[2]+p10[2]+p01[2]+p11[2])/4;
      if(!pointInRing(cx,cz,localRing)) continue;
      var tri=[[p00,p10,p11],[p00,p11,p01]];
      tri.forEach(function(t){t.forEach(function(p){positions.push(p[0],p[1]+1.8,p[2]);});});
    }
    if(!positions.length)return null;
    var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geom.computeVertexNormals();
    var mat=new THREE.MeshStandardMaterial({color:0x8d9294,roughness:.78,metalness:.02,side:THREE.DoubleSide});
    var mesh=new THREE.Mesh(geom,mat);mesh.castShadow=true;mesh.receiveShadow=true;rootGroup.add(mesh);return mesh;
  }
  function siteSurfaceSlope(x,z){
    var e=.22, a=siteHeightAt(x-e,z), b=siteHeightAt(x+e,z), c=siteHeightAt(x,z-e), d=siteHeightAt(x,z+e);
    if([a,b,c,d].some(function(v){return v===null;})) return {pitch:0,angle:0};
    var gx=(b-a)/(2*e), gz=(d-c)/(2*e), mag=Math.hypot(gx,gz); if(mag<.0001)return {pitch:0,angle:0};
    return {pitch:Math.atan(mag),angle:Math.atan2(-gx,-gz)};
  }
  function buildSiteRoofModel(panelCount){
    if(!siteRoofModel)return false;
    clearGroup(rootGroup);
    var g=siteRoofModel.dsmGrid, b=ringBounds(localRing); footprintSpan=Math.max(Math.min(Math.max(b.width,b.depth),30),7);
    roofInfo={type:'site',baseY:1.8,angle:0,pitchRad:0,halfD:Math.max(b.depth/2,.5),halfW:Math.max(b.width/2,.5),centerZ:b.cz,centerX:b.cx,minZ:b.minZ,depth:Math.max(b.depth,.5),rise:g?Math.max(Number(g.maxRelativeHeightMeters)||0,0):2.2};
    addGround();
    if(g){
      buildSiteRoofMesh();
    } else {
      // Building Insights fallback: use Google's roof-segment bounding boxes and
      // pitch/azimuth metadata when the DSM Data Layer is not covered/available.
      siteSegmentPlanes=[];
      (siteRoofModel.roof&&siteRoofModel.roof.segments||[]).forEach(function(seg){
        var fp=seg.footprint; if(!fp||fp.length<3)return;
        var poly=geoJsonRingToLocal(fp); if(!poly||poly.length<3)return;
        // Keep only the part overlapping the user's selected roof visually.
        var c=polygonCentroid(poly); if(!pointInRing(c[0],c[1],localRing))return;
        siteSegmentPlanes.push({polygon:poly,pitch:Number(seg.pitchDegrees)||0,azimuth:Number(seg.azimuthDegrees)||0,height:Number(seg.planeHeightAtCenterMeters)||0});
      });
      if(siteSegmentPlanes.length){
        siteSegmentPlanes.forEach(function(p){
          var poly=p.polygon, pitch=(p.pitch||0)*Math.PI/180, az=(p.azimuth||0)*Math.PI/180;
          var c=polygonCentroid(poly), down=[Math.sin(az),-Math.cos(az)];
          var pts=poly.map(function(q){var d=(q[0]-c[0])*down[0]+(q[1]-c[1])*down[1];return new THREE.Vector3(q[0],roofInfo.baseY+(p.height?0.03:0)+Math.tan(pitch)*d,q[1]);});
          var pos=[];for(var i=1;i<pts.length-1;i++)pos.push(pts[0].x,pts[0].y,pts[0].z,pts[i].x,pts[i].y,pts[i].z,pts[i+1].x,pts[i+1].y,pts[i+1].z);
          var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.computeVertexNormals();
          var mat=new THREE.MeshStandardMaterial({color:0x8d9294,roughness:.78,metalness:.02,side:THREE.DoubleSide});
          var mesh=new THREE.Mesh(geom,mat);mesh.castShadow=true;mesh.receiveShadow=true;rootGroup.add(mesh);
        });
      }
    }
    addShadeOverlay();
    addStructureBlocks(localObstructions.map(function(r){return{polygon:r};}),'user');
    addStructureBlocks(aiObstructions,'ai');
    addPanels(panelCount,roofInfo); updateOrientation(roofInfo); fitCamera(); return true;
  }

  function roofLocalPoint(x, z, info) {
    var q = rotatePoint([x, z], -info.angle);
    return { x: q[0], z: q[1] };
  }

  function roofHeight(x, z, info) {
    if (info && info.type === 'site') { var sh=siteHeightAt(x,z); return info.baseY + (sh===null?0:sh); }
    if (!info || info.type === 'flat') return info ? info.baseY : 0;
    var q = roofLocalPoint(x, z, info);
    var nx = Math.max(-1, Math.min(1, (q.x - info.centerX) / Math.max(info.halfW, 0.001)));
    var nz = Math.max(-1, Math.min(1, (q.z - info.centerZ) / Math.max(info.halfD, 0.001)));
    if (info.type === 'shed') {
      var t = Math.max(0, Math.min(1, (q.z - info.minZ) / Math.max(info.depth, 0.001)));
      return info.baseY + info.rise * (1 - t);
    }
    if (info.type === 'hip') {
      var edge = Math.max(Math.abs(nx), Math.abs(nz));
      return info.baseY + info.rise * (1 - Math.min(1, edge));
    }
    // Gable: ridge runs along local X; both sides meet at the centre.
    return info.baseY + info.rise * (1 - Math.abs(nz));
  }

  function roofSurfaceSlope(x, z, info) {
    if (info && info.type === 'site') return siteSurfaceSlope(x,z);
    if (!info || info.type === 'flat') return { pitch: FLAT_PANEL_TILT, angle: info ? info.angle : 0 };
    // Estimate the local roof gradient from the same height function used to
    // draw the roof. This keeps every module tangent to the rendered surface.
    var eps = 0.18;
    var hx1 = roofHeight(x + eps, z, info), hx0 = roofHeight(x - eps, z, info);
    var hz1 = roofHeight(x, z + eps, info), hz0 = roofHeight(x, z - eps, info);
    var gx = (hx1 - hx0) / (2 * eps);
    var gz = (hz1 - hz0) / (2 * eps);
    var magnitude = Math.sqrt(gx * gx + gz * gz);
    if (magnitude < 0.0001) return { pitch: 0, angle: info.angle };
    // Local +Z is deliberately pointed downhill because THREE rotation.x > 0
    // lowers the +Z edge.
    var downX = -gx / magnitude, downZ = -gz / magnitude;
    return { pitch: Math.min(info.pitchRad, Math.atan(magnitude)), angle: Math.atan2(downX, downZ) };
  }

  function addWalls(height){
    var shape=shapeFromRing(localRing);localObstructions.forEach(function(r){var h=new THREE.Path();h.moveTo(r[0][0],r[0][1]);for(var i=1;i<r.length;i++)h.lineTo(r[i][0],r[i][1]);h.closePath();shape.holes.push(h);});
    var g=new THREE.ExtrudeGeometry(shape,{depth:height,bevelEnabled:false,curveSegments:1,steps:1});g.rotateX(-Math.PI/2);
    var mesh=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:0xd9d4ce,roughness:.88,metalness:0}));mesh.castShadow=true;mesh.receiveShadow=true;rootGroup.add(mesh);
    // A thin fascia separates the building wall from the roof visually.
    var edge=localRing.map(function(p){return new THREE.Vector3(p[0],height+.01,p[1]);});edge.push(edge[0]);rootGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(edge),new THREE.LineBasicMaterial({color:0x73777a}))); 
  }

  function addRoof(info) {
    var rotated = transformRing(localRing, -info.angle);
    var outer = rotated.map(function (p) { return new THREE.Vector2(p[0], p[1]); });
    var holes = localObstructions.map(function (r) {
      return transformRing(r, -info.angle).map(function (p) { return new THREE.Vector2(p[0], p[1]); });
    });
    var contours = [outer].concat(holes);
    var all = []; contours.forEach(function (c) { all = all.concat(c); });
    var tris = THREE.ShapeUtils.triangulateShape(outer, holes);
    var pos = [];
    tris.forEach(function (t) {
      for (var k = 0; k < 3; k++) {
        var p = all[t[k]];
        var w = rotatePoint([p.x, p.y], info.angle);
        pos.push(w[0], roofHeight(w[0], w[1], info), w[1]);
      }
    });
    if (!pos.length) return;
    var g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.computeVertexNormals();
    var m = new THREE.MeshStandardMaterial({ color: 0x8b8f91, roughness: 0.76, metalness: 0.02, side: THREE.DoubleSide });
    var mesh = new THREE.Mesh(g, m);
    mesh.castShadow = true; mesh.receiveShadow = true;
    rootGroup.add(mesh);

    // Add a subtle roof edge so the plane does not visually disappear into the sky.
    var edgePts = localRing.map(function (p) { return new THREE.Vector3(p[0], roofHeight(p[0], p[1], info) + 0.015, p[1]); });
    edgePts.push(edgePts[0].clone());
    rootGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(edgePts), new THREE.LineBasicMaterial({ color: 0x62686c, transparent: true, opacity: .72 })));
  }

  function addPlaneMeshes(){
    aiPlanes.forEach(function(p,idx){if(!p.polygon||p.polygon.length<3)return;var poly=p.polygon;var pitch=(Number(p.pitch)||0)*Math.PI/180;var az=(Number.isFinite(p.azimuth)?p.azimuth:0)*Math.PI/180;
      var down=[Math.sin(az),-Math.cos(az)], c=polygonCentroid(poly), base=roofInfo.baseY+.045;
      var pts=poly.map(function(q){var d=(q[0]-c[0])*down[0]+(q[1]-c[1])*down[1];return new THREE.Vector3(q[0],base+Math.tan(pitch)*d,q[1]);});
      var pos=[];for(var i=1;i<pts.length-1;i++){pos.push(pts[0].x,pts[0].y,pts[0].z,pts[i].x,pts[i].y,pts[i].z,pts[i+1].x,pts[i+1].y,pts[i+1].z);}
      if(pos.length){var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.computeVertexNormals();var mat=new THREE.MeshStandardMaterial({color:idx%2?0x7e8f99:0x71848f,roughness:.76,metalness:.02,transparent:true,opacity:.24,side:THREE.DoubleSide});var mesh=new THREE.Mesh(geom,mat);mesh.receiveShadow=true;rootGroup.add(mesh);}
      var outline=pts.slice();outline.push(pts[0]);rootGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(outline),new THREE.LineBasicMaterial({color:0x4b87a8,transparent:true,opacity:.65})));
    });
  }

  function addStructureBlocks(structures,style){
    structures.forEach(function(item,idx){var r=item.polygon||item;if(!r||r.length<3)return;var c=polygonCentroid(r),h=Number(item.height)||1.05;var y=roofHeight(c[0],c[1],roofInfo);var g=new THREE.ExtrudeGeometry(shapeFromRing(r),{depth:h,bevelEnabled:true,bevelSize:.045,bevelThickness:.045,bevelSegments:1});g.rotateX(-Math.PI/2);g.translate(0,y,0);
      var color=style==='ai'?0xe2a33a:(idx%2?0x9fa7ad:0xaeb5ba),m=new THREE.MeshStandardMaterial({color:color,roughness:.8,transparent:style==='ai',opacity:style==='ai'?.72:1});var mesh=new THREE.Mesh(g,m);mesh.castShadow=true;mesh.receiveShadow=true;rootGroup.add(mesh);
      if(style==='ai'&&item.type){var label=makeLabel(String(item.type).slice(0,26),0xe2a33a);label.position.set(c[0],y+h+.18,c[1]);rootGroup.add(label);}
    });
  }
  function makeLabel(text,color){var canvas=document.createElement('canvas');canvas.width=512;canvas.height=96;var ctx=canvas.getContext('2d');ctx.fillStyle='rgba(255,255,255,.92)';ctx.roundRect?ctx.roundRect(8,10,496,76,16):ctx.fillRect(8,10,496,76);ctx.fill();ctx.fillStyle='#3c4349';ctx.font='700 28px Arial';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,256,48);var tex=new THREE.CanvasTexture(canvas);var mat=new THREE.SpriteMaterial({map:tex,transparent:true,depthTest:false});var s=new THREE.Sprite(mat);s.scale.set(3.2,.6,1);return s;}

  function packPanels(requested, info, roof, obstructions) {
    var best = [];
    var baseAngle = azimuthToWorldAngle(currentOrientationDeg);
    var orientations = [{ w: PANEL_W, d: PANEL_D }, { w: PANEL_D, d: PANEL_W }];
    orientations.forEach(function (o) {
      var rr = ringBounds(transformRing(roof, -baseAngle));
      var sx = o.w + PANEL_GAP, sz = o.d + PANEL_GAP, found = [];
      for (var z = rr.minZ + EDGE_SETBACK + o.d / 2; z <= rr.maxZ - EDGE_SETBACK - o.d / 2 + .001; z += sz) {
        for (var x = rr.minX + EDGE_SETBACK + o.w / 2; x <= rr.maxX - EDGE_SETBACK - o.w / 2 + .001; x += sx) {
          var world = rotatePoint([x, z], baseAngle);
          var corners = rect(world[0], world[1], o.w, o.d, baseAngle);
          if (polygonFits(corners, roof, obstructions)) found.push({ x: world[0], z: world[1], w: o.w, d: o.d, angle: baseAngle, plane: null });
        }
      }
      if (found.length > best.length) best = found;
    });
    // AI roof planes: use them only when they are valid and actually overlap the user roof.
    if (aiPlanes.length) {
      var planeBest = [];
      aiPlanes.forEach(function (p) {
        var poly = p.polygon; if (!poly || poly.length < 3) return;
        var c = polygonCentroid(poly);
        var rowAngle = Number.isFinite(p.azimuth) ? (p.azimuth * Math.PI / 180) : baseAngle;
        var rr = ringBounds(transformRing(poly, -rowAngle));
        [{ w: PANEL_W, d: PANEL_D }, { w: PANEL_D, d: PANEL_W }].forEach(function (o) {
          var arr = [], sx = o.w + PANEL_GAP, sz = o.d + PANEL_GAP;
          for (var z = rr.minZ + EDGE_SETBACK + o.d / 2; z <= rr.maxZ - EDGE_SETBACK - o.d / 2 + .001; z += sz) {
            for (var x = rr.minX + EDGE_SETBACK + o.w / 2; x <= rr.maxX - EDGE_SETBACK - o.w / 2 + .001; x += sx) {
              var world = rotatePoint([x, z], rowAngle);
              var corners = rect(world[0], world[1], o.w, o.d, rowAngle);
              if (polygonFits(corners, poly, obstructions)) arr.push({ x: world[0], z: world[1], w: o.w, d: o.d, angle: rowAngle, plane: p });
            }
          }
          if (arr.length > planeBest.length) planeBest = planeBest.concat(arr);
        });
      });
      if (planeBest.length >= Math.min(best.length, 4)) best = planeBest;
    }
    return best.slice(0, Math.max(0, Math.min(160, Number(requested) || 0)));
  }

  function azimuthToWorldAngle(azimuthDeg) {
    // Local world axes: +X = east, +Z = south. Three.js yaw=0 points the
    // module's long axis toward +Z (south), so geographic azimuth maps cleanly
    // to a yaw relative to south.
    return ((Number(azimuthDeg) || 0) - 180) * Math.PI / 180;
  }

  function addShadeOverlay(){
    if(!shadeGrid||!shadeGrid.x||!shadeGrid.z||!shadeGrid.relativeFlux)return;
    var g=shadeGrid, threshold=Number(g.threshold)||0.65;
    var mat=new THREE.MeshBasicMaterial({color:0x596168,transparent:true,opacity:.24,depthWrite:false,side:THREE.DoubleSide});
    var group=new THREE.Group();
    for(var r=0;r<g.rows-1;r++)for(var c=0;c<g.cols-1;c++){
      var vals=[g.relativeFlux[r*g.cols+c],g.relativeFlux[r*g.cols+c+1],g.relativeFlux[(r+1)*g.cols+c],g.relativeFlux[(r+1)*g.cols+c+1]].map(Number);
      if(vals.some(function(v){return !Number.isFinite(v);})||Math.min.apply(null,vals)>=threshold)continue;
      var x0=g.x[c],x1=g.x[c+1],z0=g.z[r],z1=g.z[r+1];
      var pts=[[x0,z0],[x1,z0],[x1,z1],[x0,z1]];
      var pos=[];pts.forEach(function(q){var y=siteHeightAt(q[0],q[1]);if(y===null)y=roofInfo?roofHeight(q[0],q[1],roofInfo):0;pos.push(q[0],y+.035,q[1]);});
      var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.setIndex([0,1,2,0,2,3]);geom.computeVertexNormals();group.add(new THREE.Mesh(geom,mat));
    }
    rootGroup.add(group);
  }

  function addPanels(requested, info) {
    shadeAvoidedPanels=0;
    var panels = packPanels(requested, info, localRing, localObstructions);
    var mat = new THREE.MeshStandardMaterial({ color: 0x124f91, roughness: .24, metalness: .34 });
    var frameMat = new THREE.MeshStandardMaterial({ color: 0x1e252b, roughness: .4, metalness: .62 });
    panels.forEach(function (p) {
      var slope = roofSurfaceSlope(p.x, p.z, info);
      if (p.plane && Number.isFinite(Number(p.plane.pitch))) slope.pitch = Math.min(info.pitchRad || Math.PI/4, Math.abs(Number(p.plane.pitch) * Math.PI/180));
      var pitch = slope.pitch;
      // The compass controls the electrical-facing azimuth of the PV array.
      // The roof pitch is retained so the visual remains a plausible mounted
      // system rather than changing the building geometry itself.
      var angle = azimuthToWorldAngle(currentOrientationDeg);
      var y = roofHeight(p.x, p.z, info) + .10;
      var group = new THREE.Group();
      var panel = new THREE.Mesh(new THREE.BoxGeometry(p.w, PANEL_THICKNESS, p.d), mat);
      var frame = new THREE.Mesh(new THREE.BoxGeometry(p.w + .055, .026, p.d + .055), frameMat);
      frame.position.y = -.012; group.add(frame); group.add(panel);
      group.rotation.order = 'YXZ';
      group.rotation.y = angle;
      group.rotation.x = pitch;
      group.position.set(p.x, y, p.z);
      group.castShadow = true;
      rootGroup.add(group);
      if (info.type === 'flat' && !p.plane) {
        var r1 = new THREE.Mesh(new THREE.BoxGeometry(p.w + .08, .035, .035), frameMat);
        var r2 = r1.clone();
        r1.position.set(0, -.08, -p.d * .31); r2.position.set(0, -.08, p.d * .31);
        group.add(r1); group.add(r2);
      }
    });
    updatePanelStatus(panels.length, requested);
    return panels.length;
  }

  function addGround(){var w=Math.max(footprintSpan*1.7,16),d=Math.max(footprintSpan*1.45,14);var g=new THREE.Mesh(new THREE.PlaneGeometry(w,d),new THREE.MeshStandardMaterial({color:0xdfe3df,roughness:1}));g.rotation.x=-Math.PI/2;g.position.y=-.04;g.receiveShadow=true;rootGroup.add(g);}

  function buildRoofModel(pitchDeg,panelCount){if(!rootGroup)return;currentPitch=Math.max(0,Math.min(40,Number(pitchDeg)||0));currentPanels=panelCount;if(siteRoofModel){buildSiteRoofModel(currentPanels);return;}clearGroup(rootGroup);
    if(!localRing||localRing.length<3)localRing=[[-6,-4],[6,-4],[6,4],[-6,4]];
    var bounds=ringBounds(localRing);footprintSpan=Math.max(Math.min(Math.max(bounds.width,bounds.depth),30),7);var angle=principalAngle(localRing),rotated=transformRing(localRing,-angle),rb=ringBounds(rotated),effective=roofType==='flat'?0:currentPitch;
    var riseRun=(roofType==='shed'?rb.depth:(roofType==='hip'?Math.min(rb.width,rb.depth)/2:rb.depth/2));
    var info={type:roofType,baseY:2.35,angle:angle,pitchRad:effective*Math.PI/180,halfD:Math.max(rb.depth/2,.5),halfW:Math.max(rb.width/2,.5),centerZ:rb.cz,centerX:rb.cx,minZ:rb.minZ,depth:Math.max(rb.depth,.5),rise:Math.tan(effective*Math.PI/180)*riseRun};roofInfo=info;
    addGround();addWalls(info.baseY);addRoof(info);addPlaneMeshes();addStructureBlocks(localObstructions.map(function(r){return{polygon:r};}),'user');addStructureBlocks(aiObstructions,'ai');addPanels(currentPanels,info);updateOrientation(info);fitCamera();
  }
  function cardinalDirection(angle){var deg=((angle*180/Math.PI)+360)%360;var dirs=['N','NE','E','SE','S','SW','W','NW'];return dirs[Math.round(deg/45)%8];}
  function updateCompassMarker(){
    var ring=document.querySelector('.roof3d-compass-ring'),sun=document.querySelector('.roof3d-sun');
    if(!ring||!sun)return;
    var deg=((Number(currentOrientationDeg)||0)%360+360)%360,rad=deg*Math.PI/180;
    // Keep the handle on the visible outer ellipse.
    sun.style.left=(50+42*Math.sin(rad))+'%';
    sun.style.top=(50-38*Math.cos(rad))+'%';
    sun.style.bottom='auto';
    sun.style.transform='translate(-50%,-50%)';
    sun.setAttribute('aria-label','Solar orientation '+Math.round(deg)+' degrees');
    sun.title='Drag to change solar orientation';
  }

  function updateOrientation(info){
    var t=document.getElementById('roof3d-orientation'),d=document.getElementById('roof3d-orientation-detail');
    if(!t||!d)return;
    var heading=((Number(currentOrientationDeg)||0)%360+360)%360;
    var pitch=Math.round(Number(aiAnalysis?.pitch_degrees ?? currentPitch)||0);
    t.textContent='Solar orientation · '+cardinalDirection(heading*Math.PI/180)+' · '+Math.round(heading)+'°';
    d.textContent=roofType==='flat'?'Panels tilted '+Math.round(FLAT_PANEL_TILT*180/Math.PI)+'° · drag the compass to optimize':'Panels facing '+Math.round(heading)+'° · '+pitch+'° roof pitch · drag the compass';
    var detail=document.getElementById('roof3d-ai-details');
    if(detail){
      var aiText=aiAnalysis?' AI vision: '+(aiAnalysis.roof_type||roofType)+' roof, '+pitch+'° pitch, advisory confidence '+Math.round((Number(aiAnalysis.confidence)||0)*100)+'%.':'';
      detail.textContent=aiText+' Orientation preview: '+Math.round(heading)+'° ('+cardinalDirection(heading*Math.PI/180)+').';
    }
    updateCompassMarker();
  }

  function updateOrientationStatus(result){
    if(!result)return;
    var gen=Number(result.annual_generation);
    var savings=Number(result.monthly_savings);
    var factor=Number(result.orientation_factor);
    var label=result.orientation_label||cardinalDirection(currentOrientationDeg*Math.PI/180);
    if(Number.isFinite(gen)){
      document.querySelectorAll('[data-orientation-annual-generation]').forEach(function(el){el.textContent=Math.round(gen).toLocaleString('en-IN');});
    }
    if(Number.isFinite(savings)){
      document.querySelectorAll('[data-orientation-monthly-savings]').forEach(function(el){el.textContent='₹'+Math.round(savings).toLocaleString('en-IN');});
    }
    var factorEl=document.getElementById('roof3d-orientation-factor');
    if(factorEl&&Number.isFinite(factor))factorEl.textContent=Math.round(factor*100)+'% relative yield';
    var deltaEl=document.getElementById('roof3d-orientation-delta');
    if(deltaEl&&Number.isFinite(gen)&&typeof ANNUAL_GENERATION!=='undefined'){
      var delta=gen-savedAnnualGeneration;
      deltaEl.textContent=(delta>=0?'+':'')+Math.round(delta).toLocaleString('en-IN')+' kWh/yr vs saved orientation';
    }
    var apply=document.getElementById('roof3d-orientation-apply');
    if(apply)apply.disabled=false;
    var status=document.getElementById('roof-ai-status');
    if(status)status.textContent='Orientation preview · '+label+' · '+Math.round((factor||0)*100)+'% relative yield. Click Apply orientation to save.';
  }

  function requestOrientationPreview(){
    var locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null;
    if(!locationId)return;
    var seq=++orientationRequestSeq;
    var status=document.getElementById('roof-ai-status');if(status)status.textContent='Calculating generation for '+Math.round(currentOrientationDeg)+'°…';
    var csrf=document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')||'';
    fetch('/api/estimate/'+encodeURIComponent(locationId)+'/orientation-preview',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf,'X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({orientation:currentOrientationDeg})})
      .then(function(res){return res.json().then(function(data){return{ok:res.ok,data:data};});})
      .then(function(out){if(seq!==orientationRequestSeq)return;if(!out.ok)throw new Error(out.data.error||'Preview failed');updateOrientationStatus(out.data);})
      .catch(function(err){if(seq!==orientationRequestSeq)return;var status=document.getElementById('roof-ai-status');if(status)status.textContent='Orientation changed visually. Generation preview unavailable right now.';console.warn('Orientation preview failed',err);});
  }

  function setOrientation(deg, preview){
    var n=Number(deg);if(!Number.isFinite(n))return;
    currentOrientationDeg=((n%360)+360)%360;orientationDirty=Math.abs(currentOrientationDeg-savedOrientationDeg)>0.1;
    buildRoofModel(currentPitch,currentPanels);
    if(preview){clearTimeout(orientationPreviewTimer);orientationPreviewTimer=setTimeout(requestOrientationPreview,180);}
  }

  function bindOrientationCompass(){
    var ring=document.querySelector('.roof3d-compass-ring');if(!ring)return;
    var dragging=false;
    function readDeg(e){var r=ring.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2,dx=e.clientX-cx,dy=e.clientY-cy;var deg=Math.atan2(dx,-dy)*180/Math.PI;return(deg+360)%360;}
    function move(e){if(!dragging)return;setOrientation(readDeg(e),true);}
    ring.addEventListener('pointerdown',function(e){dragging=true;userInteracted=true;ring.setPointerCapture?.(e.pointerId);setOrientation(readDeg(e),true);e.preventDefault();});
    ring.addEventListener('pointermove',move);
    ['pointerup','pointercancel','lostpointercapture'].forEach(function(ev){ring.addEventListener(ev,function(){dragging=false;});});
    ring.setAttribute('role','slider');ring.setAttribute('tabindex','0');ring.setAttribute('aria-label','Solar panel orientation');
    ring.addEventListener('keydown',function(e){var step=e.shiftKey?15:5;if(e.key==='ArrowLeft'||e.key==='ArrowDown'){setOrientation(currentOrientationDeg-step,true);e.preventDefault();}else if(e.key==='ArrowRight'||e.key==='ArrowUp'){setOrientation(currentOrientationDeg+step,true);e.preventDefault();}else if(e.key==='Home'){setOrientation(180,true);e.preventDefault();}});
    updateCompassMarker();
  }

  async function applyOrientation(){
    if(!orientationDirty)return;
    var locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null;if(!locationId)return;
    var apply=document.getElementById('roof3d-orientation-apply');if(apply){apply.disabled=true;apply.textContent='Saving…';}
    var csrf=document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')||'';
    try{
      var res=await fetch('/api/estimate/'+encodeURIComponent(locationId)+'/orientation',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf,'X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({orientation:currentOrientationDeg})});
      var data=await res.json();if(!res.ok)throw new Error(data.error||'Could not save orientation');
      savedOrientationDeg=currentOrientationDeg;savedAnnualGeneration=Number(data.annual_generation||savedAnnualGeneration);orientationDirty=false;updateOrientationStatus(data);
      document.querySelectorAll('[data-orientation-annual-generation]').forEach(function(el){el.dataset.original=el.textContent;});
      if(apply){apply.textContent='✓ Orientation applied';setTimeout(function(){apply.textContent='Apply orientation';},1400);}
      var banner=document.getElementById('orientation-save-status');if(banner)banner.textContent='Saved to this estimate.';
    }catch(err){if(apply){apply.disabled=false;apply.textContent='Apply orientation';}var status=document.getElementById('roof-ai-status');if(status)status.textContent='Could not save orientation: '+err.message;}
  }

  function undoOrientation(){setOrientation(savedOrientationDeg,false);orientationDirty=false;var apply=document.getElementById('roof3d-orientation-apply');if(apply){apply.disabled=true;apply.textContent='Apply orientation';}requestOrientationPreview();}
  function updatePanelStatus(placed,requested){var e=document.getElementById('roof-3d-panel-status');var shadeText=shadeAvoidedPanels>0?' '+shadeAvoidedPanels+' candidate positions excluded for low-sun/shaded areas.':'';if(e)e.textContent=requested&&placed<requested?placed+' of '+requested+' modules fit after roof setbacks, obstructions and shade.'+shadeText:placed+' physical PV modules placed on the selected roof.'+shadeText;var c=document.getElementById('roof3d-module-count');if(c)c.textContent=String(placed||0);}
  function parseGeometryFromGlobals(){localRing=null;localObstructions=[];var r=geoJsonRingToLocal(extractRing(typeof ROOF_POLYGON_GEOJSON!=='undefined'?ROOF_POLYGON_GEOJSON:null));if(r)localRing=r;var obs=typeof ROOF_OBSTRUCTIONS_GEOJSON!=='undefined'?ROOF_OBSTRUCTIONS_GEOJSON:null;if(obs&&obs.coordinates){var list=obs.type==='MultiPolygon'?obs.coordinates:[obs.coordinates];list.forEach(function(item){var loc=geoJsonRingToLocal(item[0]||item);if(loc&&loc.length>=3)localObstructions.push(loc);});}}
  function fitCamera(){if(!camera)return;cameraDistance=Math.max(footprintSpan*1.22,8.5);cameraAzimuth=.72;cameraElevation=.48;updateCamera();}
  function updateCamera(){if(!camera)return;var targetY=roofInfo?(roofInfo.baseY+Math.max(roofInfo.rise,0)*.34):2.0,ce=Math.cos(cameraElevation),se=Math.sin(cameraElevation);camera.position.set(Math.cos(cameraAzimuth)*ce*cameraDistance,targetY+se*cameraDistance,Math.sin(cameraAzimuth)*ce*cameraDistance);camera.lookAt(0,targetY,0);}
  function bindCameraControls(container){var dragging=false,lastX=0,lastY=0;container.addEventListener('pointerdown',function(e){dragging=true;userInteracted=true;lastX=e.clientX;lastY=e.clientY;container.setPointerCapture?.(e.pointerId);});container.addEventListener('pointermove',function(e){if(!dragging)return;var dx=e.clientX-lastX,dy=e.clientY-lastY;lastX=e.clientX;lastY=e.clientY;cameraAzimuth-=dx*.008;cameraElevation=Math.max(.16,Math.min(1.18,cameraElevation+dy*.006));updateCamera();});['pointerup','pointercancel'].forEach(function(ev){container.addEventListener(ev,function(){dragging=false;});});container.addEventListener('wheel',function(e){e.preventDefault();userInteracted=true;cameraDistance=Math.max(Math.max(footprintSpan*.56,6.5),Math.min(Math.max(footprintSpan*2.6,26),cameraDistance*(1+e.deltaY*.0008)));updateCamera();},{passive:false});}
  function setRoofType(type){roofType=['flat','gable','hip','shed','mixed'].indexOf(type)>=0?type:'flat';buildRoofModel(currentPitch,currentPanels);}
  function applyAIAnalysis(a){if(!a||typeof a!=='object')return;aiAnalysis=a;aiStructuresApplied=false;var bbox=a.imagery_bbox;aiPlanes=[];aiObstructions=[];
    function normToLocal(poly){if(!bbox||!Array.isArray(bbox)||bbox.length!==4)return null;var minLng=bbox[0],minLat=bbox[1],maxLng=bbox[2],maxLat=bbox[3];var geojson=poly.map(function(p){var lng=minLng+(Number(p[0])/1000)*(maxLng-minLng),lat=maxLat-(Number(p[1])/1000)*(maxLat-minLat);return[lng,lat];});return geoJsonRingToLocal(geojson);}
    (a.roof_planes||[]).forEach(function(p){var loc=normToLocal(p.polygon||[]);if(loc&&loc.length>=3)aiPlanes.push({polygon:loc,pitch:Number(p.pitch_degrees)||0,azimuth:Number.isFinite(Number(p.azimuth_degrees))?Number(p.azimuth_degrees):null,confidence:Number(p.confidence)||0});});
    (a.obstructions||[]).forEach(function(o){var loc=normToLocal(o.polygon||[]);if(loc&&loc.length>=3)aiObstructions.push({polygon:loc,type:o.type||'detected structure',confidence:Number(o.confidence)||0,height:1.0});});
    if(a.roof_type&&a.roof_type!=='unknown'){roofType=String(a.roof_type).toLowerCase();var ss=document.getElementById('roof-shape-select');if(ss)ss.value=roofType;}
    var pitch=Number(a.pitch_degrees);if(Number.isFinite(pitch)){currentPitch=Math.max(0,Math.min(40,pitch));var ps=document.getElementById('roof-pitch-select');if(ps){var vals=[0,5,10,15,20,25,30,35,40],near=vals.reduce(function(x,y){return Math.abs(y-pitch)<Math.abs(x-pitch)?y:x;},0);ps.value=String(near);}}
    buildRoofModel(currentPitch,currentPanels);
  }
  function applyAIStructures(){
    if(!aiObstructions.length)return;
    aiObstructions.forEach(function(item){
      if(!item.polygon||item.polygon.length<3)return;
      var center=polygonCentroid(item.polygon), duplicate=localObstructions.some(function(o){var c=polygonCentroid(o);return Math.hypot(c[0]-center[0],c[1]-center[1])<0.65;});
      if(!duplicate)localObstructions.push(item.polygon);
    });
    aiStructuresApplied=true;
    var b=document.getElementById('roof-ai-apply-btn');if(b)b.textContent='✓ Structures applied';
    var s=document.getElementById('roof-ai-status');if(s)s.textContent='AI-detected rooftop structures are now excluded from panel placement.';
    buildRoofModel(currentPitch,currentPanels);
  }

  async function loadSatelliteReference(){
    if(!localRing||!window.ROOF_POLYGON_GEOJSON)return;
    var badge=document.getElementById('roof3d-satellite-badge');
    try{var res=await fetch('/api/roof-ai/imagery',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'image/jpeg','X-Requested-With':'XMLHttpRequest','X-CSRFToken':document.querySelector('meta[name=\"csrf-token\"]')?.getAttribute('content')||''},body:JSON.stringify({polygon:ROOF_POLYGON_GEOJSON.coordinates?.[0]||[]})});if(!res.ok)throw new Error('imagery unavailable');var blob=await res.blob(),url=URL.createObjectURL(blob);var loader=new THREE.TextureLoader();loader.load(url,function(tex){satelliteTexture=tex;var overlay=new THREE.Mesh(new THREE.PlaneGeometry(footprintSpan*1.28,footprintSpan*1.28),new THREE.MeshBasicMaterial({map:tex,transparent:true,opacity:.20,depthWrite:false}));overlay.rotation.x=-Math.PI/2;overlay.position.y=-.02;overlay.scale.set(1,.72,1);rootGroup.add(overlay);if(badge){badge.textContent='Satellite reference · ready';badge.classList.add('is-ready');}});}catch(e){if(badge){badge.textContent='Satellite reference · unavailable';badge.classList.add('is-error');}}
  }
  async function loadSiteRoofModel(){
    var locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null; if(!locationId)return;
    var badge=document.getElementById('roof3d-satellite-badge'), status=document.getElementById('roof-ai-status');
    siteModelLoading=true;
    if(badge){badge.textContent='Actual site model · loading…';badge.classList.remove('is-error');}
    if(status)status.textContent='Reconstructing the selected rooftop from location-specific elevation data…';
    try{
      var res=await fetch('/api/roof-model/'+encodeURIComponent(locationId)+'?high_fidelity=1',{credentials:'same-origin',headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});
      var data=await res.json(); if(!res.ok||!data.available)throw new Error(data.error||'Location-specific roof data unavailable');
      siteRoofModel=data; siteModelLoading=false;
      shadeGrid=data.shade||null; shadeSide=shadeGrid&&shadeGrid.shadeSide?shadeGrid.shadeSide:null;
      var segs=(data.roof&&data.roof.segments)||[];
      if(badge){badge.textContent=data.dsmAvailable?'Actual site roof · DSM':(data.shadeAvailable?'Actual site roof · Solar roof segments + shade map':'Actual site roof · Solar roof segments');badge.classList.add('is-ready');}
      var shadeBadge=document.getElementById('roof3d-shade-badge');
      if(shadeBadge){if(shadeGrid&&shadeSide){shadeBadge.textContent='Shade-aware · low-sun side '+shadeSide+' · panels avoid shaded zone';shadeBadge.classList.add('is-ready');shadeBadge.classList.remove('is-error');}else{shadeBadge.textContent='Shade analysis · unavailable at this location';shadeBadge.classList.add('is-error');}}
      if(status){
        var sourceText=data.dsmAvailable?'high-resolution elevation data':'Google roof-segment data';
        status.textContent='Actual site roof reconstructed from '+sourceText+' · '+segs.length+' roof plane'+(segs.length===1?'':'s')+' detected.'+(shadeGrid&&shadeSide?' · Shaded/low-sun side: '+shadeSide+'. Panels avoid that zone.':'')+(data.warnings&&data.warnings.length?' '+data.warnings[0]: '');
      }
      buildRoofModel(currentPitch,currentPanels);
    }catch(err){siteModelLoading=false;shadeGrid=null;shadeSide=null;var shadeBadge=document.getElementById('roof3d-shade-badge');if(shadeBadge){shadeBadge.textContent='Shade analysis · unavailable';shadeBadge.classList.add('is-error');}if(badge){badge.textContent='Actual site roof · unavailable';badge.classList.add('is-error');}if(status)status.textContent='Site roof request failed: '+err.message+' (check Solar API access/coverage).';console.warn('Site roof reconstruction failed',err);}
  }

  function init(){var container=document.getElementById('roof-3d-container'),fallback=document.getElementById('roof-3d-fallback');if(!container)return;if(typeof THREE==='undefined'||!hasWebGL()){if(fallback){fallback.classList.remove('hidden');fallback.style.display='block';}return;}if(fallback){fallback.classList.add('hidden');fallback.style.display='none';}
    parseGeometryFromGlobals();var width=container.clientWidth||600,height=container.clientHeight||360;scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(38,width/height,.1,1000);renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));renderer.setSize(width,height);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;container.appendChild(renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xeaf5ff,0x6c6259,1.3));var sun=new THREE.DirectionalLight(0xffe7bf,2.35);sun.position.set(12,24,15);sun.castShadow=true;sun.shadow.mapSize.width=2048;sun.shadow.mapSize.height=2048;scene.add(sun);rootGroup=new THREE.Group();scene.add(rootGroup);
    var ps=document.getElementById('roof-pitch-select'),ss=document.getElementById('roof-shape-select');currentPitch=ps?Number(ps.value)||0:0;roofType=ss?ss.value:'flat';currentPanels=Number(typeof PANEL_COUNT!=='undefined'?PANEL_COUNT:0)||0;buildRoofModel(currentPitch,currentPanels);bindCameraControls(container);bindOrientationCompass();loadSatelliteReference();loadSiteRoofModel();
    if(ps)ps.addEventListener('change',function(){currentPitch=Number(this.value)||0;aiPlanes=[];buildRoofModel(currentPitch,currentPanels);});if(ss)ss.addEventListener('change',function(){roofType=this.value;aiPlanes=[];buildRoofModel(currentPitch,currentPanels);});
    function animate(){animationId=requestAnimationFrame(animate);if(rootGroup&&!userInteracted)rootGroup.rotation.y+=.0007;renderer.render(scene,camera);}animate();window.addEventListener('resize',function(){var w=container.clientWidth,h=container.clientHeight;if(w<10||h<10)return;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);});
    window.SIGNIX_ROOF3D={setPitch:function(d){currentPitch=Number(d)||0;buildRoofModel(currentPitch,currentPanels);},setPanelCount:function(n){currentPanels=Number(n)||0;buildRoofModel(currentPitch,currentPanels);},setPitchAndPanels:function(d,n){currentPitch=Number(d)||0;currentPanels=Number(n)||0;buildRoofModel(currentPitch,currentPanels);},setRoofType:setRoofType,setOrientation:setOrientation,applyOrientation:applyOrientation,undoOrientation:undoOrientation,applyAIAnalysis:applyAIAnalysis,applyAIStructures:applyAIStructures,hasRealFootprint:function(){return!!(localRing&&localRing.length>=3);},getPlacedPanelCount:function(){return document.getElementById('roof-3d-panel-status')?.textContent||'';},loadSiteRoofModel:loadSiteRoofModel};
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
