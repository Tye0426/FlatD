'use strict';
const $ = s => document.querySelector(s);
const element = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const defaults = {
  cars: [{id:'A',destination:'1',length:15},{id:'B',destination:'2',length:15},{id:'C',destination:'3',length:15},{id:'D',destination:'1',length:15}],
  tracks: [{name:'I',capacity:120,initial:['D','C'],goal:['A','D','B','C']},{name:'II',capacity:120,initial:['B','A'],goal:[]},{name:'III',capacity:120,initial:[],goal:[]}],
  headshunt:100, locomotive:20, timeLimit:10, objective:'time', weight:350, poolSize:100, initialTrack:1
};
let cars = [], tracks = [], result = null, currentStep = 0, busy = false;
const fields = ['headshunt','locomotive','timeLimit','objective','weight','poolSize','initialTrack'];
const secs = v => `${v.toFixed(2)} 秒`;
function duration(v) { const cents = Math.round(v * 100), m = Math.floor(cents / 6000), s = (cents % 6000) / 100; return m ? `${m}分 ${s.toFixed(2)}秒` : `${s.toFixed(2)}秒`; }
function status(text, kind = '') { $('#solveStatus').className = `status-pill ${kind}`; $('#solveStatus').textContent = text; }
function error(message) { $('#validation').textContent = message; $('#validation').classList.remove('hidden'); status('请检查输入', 'error'); }
function clearError() { $('#validation').classList.add('hidden'); }
function invalidate() {
  if (result) { result = null; $('#solutionResult').classList.add('hidden'); $('#emptyResult').classList.remove('hidden'); $('#downloadResult').disabled = true; }
  status('配置待求解');
}
function switchView(name) {
  document.querySelectorAll('.app-view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));
  document.querySelectorAll('.nav-tab').forEach(v=>{const on=v.dataset.view===name;v.classList.toggle('active',on);v.setAttribute('aria-current',on?'step':'false');});
  window.scrollTo({top:0,behavior:'smooth'});
}
const roman = value => {
  const pairs=[[1000,'M'],[900,'CM'],[500,'D'],[400,'CD'],[100,'C'],[90,'XC'],[50,'L'],[40,'XL'],[10,'X'],[9,'IX'],[5,'V'],[4,'IV'],[1,'I']];
  let n=value,out='';for(const [v,s] of pairs)while(n>=v){out+=s;n-=v;}return out;
};
const nextCarId = () => { for(let n=0;;n++){let x=n,s='';do{s=String.fromCharCode(65+x%26)+s;x=Math.floor(x/26)-1;}while(x>=0);if(!cars.some(c=>c.id===s))return s;} };
const splitIds = text => text.split(/[,，\s]+/).filter(Boolean);
function deleteVehicle(id){
  if(cars.length<=1)return error('至少保留一辆车。');cars=cars.filter(c=>c.id!==id);tracks.forEach(t=>{t.initialText=splitIds(t.initialText).filter(x=>x!==id).join(',');t.goalText=splitIds(t.goalText).filter(x=>x!==id).join(',');});renderEditors();invalidate();clearError();$('#dragHint').textContent=`车辆 ${id} 已删除，初始和目标状态已同步`;renderInitialPreview();
}
function deleteTrack(index){
  if(tracks.length<=2)return error('算法至少需要两条股道，不能继续删除。');const target=index>0?index-1:1,lengths=Object.fromEntries(cars.map(c=>[c.id,Number(c.length)||0]));
  const merged={};for(const field of ['initialText','goalText']){merged[field]=[...splitIds(tracks[target][field]),...splitIds(tracks[index][field])];if(YardModel.occupied(merged[field],lengths)>Number(tracks[target].capacity)+1e-8)return error(`${tracks[target].name}道容量不足，无法接收被删除股道的${field==='initialText'?'初始':'目标'}车辆。`);}for(const field of Object.keys(merged))tracks[target][field]=merged[field].join(',');
  const oldPosition=Number($('#initialTrack').value)||1;tracks.splice(index,1);tracks.forEach((t,i)=>t.name=roman(i+1));$('#initialTrack').value=oldPosition-1===index?Math.min(index,tracks.length-1)+1:oldPosition-1>index?oldPosition-1:oldPosition;renderEditors();invalidate();clearError();$('#dragHint').textContent='股道已删除，原有车辆已转入相邻股道';renderInitialPreview();
}
function editInput(value, label, numeric, onChange) {
  const el = element('input'); el.value = value; el.type = numeric ? 'number' : 'text'; el.setAttribute('aria-label', label);
  el.addEventListener('input', () => { onChange(numeric ? Number(el.value) : el.value.trim()); invalidate(); renderInitialPreview(); }); return el;
}
function removeButton(label, onClick) { const b = element('button','icon-button','×'); b.type = 'button'; b.setAttribute('aria-label',label); b.addEventListener('click',onClick); return b; }
function header(container, cls, labels) { const h = element('div',`${cls} row-head`); labels.forEach(t => h.append(element('span','',t))); container.replaceChildren(h); }
function renderEditors() {
  header($('#carsEditor'),'data-row',['车辆ID','去向','长度(m)','']);
  cars.forEach((c,i) => { const row = element('div','data-row');
    row.append(editInput(c.id,`第${i+1}辆车ID`,false,v=>c.id=v), editInput(c.destination,`第${i+1}辆车去向`,false,v=>c.destination=v), editInput(c.length,`第${i+1}辆车长度`,true,v=>c.length=v), removeButton(`删除车辆${c.id}`,()=>deleteVehicle(c.id))); $('#carsEditor').append(row);
  });
  header($('#initialTracksEditor'),'initial-track-row',['股道','容量(m)','初始车辆顺序','']);
  header($('#goalTracksEditor'),'goal-track-row',['股道','最终车辆顺序']);
  tracks.forEach((t,i) => { const row=element('div','track-row');
    const nameCell=element('div','track-id-cell',roman(i+1));t.name=roman(i+1);
    row.className='initial-track-row';row.append(nameCell,editInput(t.capacity,`第${i+1}股道容量`,true,v=>t.capacity=v),editInput(t.initialText,`第${i+1}股道初始状态`,false,v=>t.initialText=v),removeButton(`删除股道${t.name}`,()=>deleteTrack(i)));$('#initialTracksEditor').append(row);
    const goalRow=element('div','goal-track-row');goalRow.append(element('div','track-id-cell',roman(i+1)),editInput(t.goalText,`第${i+1}股道目标状态`,false,v=>t.goalText=v));$('#goalTracksEditor').append(goalRow);
  });
  $('#initialTrack').max=tracks.length;
}
function applyConfig(raw) {
  // Validate before changing visible state: invalid agent input is transactional.
  const c=ShuntingSolver.validate(raw); cars=c.cars;
  tracks=c.tracks.map((t,i)=>({...t,name:roman(i+1),initialText:t.initial.join(','),goalText:t.goal.join(',')}));
  fields.forEach(id=>$('#'+id).value=c[id]); renderEditors(); invalidate(); clearError(); updateFormula(); renderInitialPreview();
}
function collect() {
  const c={cars:structuredClone(cars),tracks:tracks.map(t=>({name:t.name,capacity:t.capacity,initial:t.initialText.split(/[,，\s]+/).filter(Boolean),goal:t.goalText.split(/[,，\s]+/).filter(Boolean)}))};
  fields.forEach(id=>c[id]=id==='objective'?$('#'+id).value:Number($('#'+id).value)); return ShuntingSolver.validate(c);
}
function updateFormula() { $('#priorityFormula').textContent=$('#objective').value==='time'?`η = T + ${$('#weight').value} × Λ`:`η = 轮数 + ${$('#weight').value} × Λ（扩展对照）`; }
function lock(value) { busy=value; document.querySelectorAll('.config-panel input,.config-panel select,.config-panel button').forEach(e=>e.disabled=value); $('#solveButton span').textContent=value?'正在搜索…':'开始求解'; }
async function runSolver() {
  if(busy)throw Error('已有求解任务在运行。'); clearError(); let config;
  try { config=collect(); } catch(e) { error(e.message); return null; }
  invalidate(); lock(true); status('正在求解','working');
  try {
    if(typeof Worker==='undefined')throw Error('当前浏览器不支持Web Worker，请用现代浏览器通过HTTP/HTTPS打开网站。');
    const answer=await new Promise((resolve,reject)=>{
      const worker=new Worker('./solver-worker.js');
      worker.onmessage=({data})=>{
        if(data.type==='progress') status(`搜索中 · ${data.stats.expanded} 个节点`,'working');
        else { worker.terminate(); if(data.type==='result')resolve(data.result);else reject(Error(data.message)); }
      };
      worker.onerror=e=>{worker.terminate();reject(Error(e.message||'无法加载算法文件。请将solver.js与solver-worker.js一并部署。'));};
      worker.postMessage(config);
    });
    renderResult(answer); switchView('solution'); return answer;
  } catch(e) { error(e.message); return null; } finally { lock(false); }
}
function labelStatus(r) { return {optimal:'当前模型最优',feasible:'当前可行解',infeasible:'当前模型无解',no_solution_found:'预算内未找到'}[r.status]; }
function renderResult(answer) {
  result=answer; currentStep=0; $('#emptyResult').classList.add('hidden'); $('#solutionResult').classList.remove('hidden'); $('#downloadResult').disabled=false;
  $('#metricStatus').textContent=labelStatus(result);
  $('#metricTime').textContent=result.totalTimeSeconds===null?'暂无完整方案':duration(result.totalTimeSeconds);
  $('#metricSeconds').textContent=result.totalTimeSeconds===null?'—':`模型估计合计 ${secs(result.totalTimeSeconds)}`;
  $('#metricSteps').textContent=result.roundCount===null?'—':`${result.roundCount} 轮 / ${result.operationCount} 步`;
  $('#metricCompute').textContent=secs(result.statistics.elapsedSeconds);
  const term={exhausted:'搜索结束',initial_is_goal:'初始即为目标',time_limit:'达到时间上限',node_limit:'达到外层节点上限',san_node_limit:'达到SAN分支节点上限'}[result.termination]||result.termination;
  $('#resultContext').textContent=`目标：${result.objective==='time'?'最短总调车时间':'最少宏动作轮数（非论文时间目标）'}；本次 w = ${result.weight}；每个源股道解池 = ${result.poolSize||'不截断'}；扩展 ${result.statistics.expanded} 个状态 / ${result.statistics.sanModels} 个SAN子问题。${term}。${result.candidatePoolRestricted?'存在解池截断，不能证明全局最优。':result.optimalityProven?'最优性仅限当前模型与固定目标，不含跨轮推牵合并。':'尚未获得最优性证明。'}`;
  $('#timeBreakdown').replaceChildren();
  if(result.timeBreakdown)for(const [field,label] of [['ladderSeconds','梯线走行'],['entrySeconds','进入股道'],['exitSeconds','退出股道']])$('#timeBreakdown').append(element('span','',`${label} ${secs(result.timeBreakdown[field])}`));
  $('#timeline').replaceChildren();
  function item(i,title,detail) { const li=element('li'), b=element('button'); b.type='button'; b.append(element('strong','',title),element('br'),document.createTextNode(detail)); b.addEventListener('click',()=>displayStep(i));li.append(b);$('#timeline').append(li); }
  item(0,'初始状态','累计用时 0.00 秒');
  result.operations.forEach(op=>item(op.step,`${op.step}. ${op.kind==='PULL'?'牵出 / 连挂':'推送 / 摘挂'} · ${op.trackName}道（${op.track}号）`,`${op.cars.join('、')} · 第${op.round}轮 · 本步${secs(op.durationSeconds)} · 累计${secs(op.cumulativeSeconds)}`));
  $('#timeTable').replaceChildren();
  result.operations.forEach(op=>{const tr=element('tr');[`${op.step} / ${op.round}`,op.kind==='PULL'?'牵出':'推送',`${op.track} · ${op.trackName}`,op.cars.join('、'),op.ladderSeconds.toFixed(2),op.entrySeconds.toFixed(2),op.exitSeconds.toFixed(2),op.durationSeconds.toFixed(2),op.cumulativeSeconds.toFixed(2)].forEach(t=>tr.append(element('td','',t)));$('#timeTable').append(tr);});
  if(result.totalTimeSeconds!==null){const tr=element('tr');['合计','','','',result.timeBreakdown.ladderSeconds.toFixed(2),result.timeBreakdown.entrySeconds.toFixed(2),result.timeBreakdown.exitSeconds.toFixed(2),result.totalTimeSeconds.toFixed(2),result.totalTimeSeconds.toFixed(2)].forEach(t=>tr.append(element('th','',t)));$('#timeTable').append(tr);}
  displayStep(0); status(labelStatus(result),result.status==='optimal'?'success':result.status==='infeasible'?'error':'');
}
let yardDrag=null;
function yardCar(id,destinations,lengths,pxPerMetre,editMode=null){
  const e=element('span',`car${editMode?' draggable-car':''}`,id);e.dataset.dest=destinations[id]||'?';e.dataset.carId=id;e.title=`车辆${id} · 去向${destinations[id]||'未定义'} · ${lengths[id]||0}米`;
  e.style.width=`${Math.max(5,(lengths[id]||0)*pxPerMetre)}px`;if(editMode){e.dataset.editMode=editMode;e.tabIndex=0;e.setAttribute('role','button');e.setAttribute('aria-label',`拖动车辆${id}改变${editMode==='initial'?'初始':'最终'}位置`);e.addEventListener('pointerdown',startYardDrag);}return e;
}
function renderYardInto(container,state,meta,destinations,options={}){
  container.replaceChildren();const n=state.tracks.length,capacities=meta.map(t=>Number(t.capacity)||0),lengths=options.lengths||Object.fromEntries((result?.parameters?.cars||[]).map(c=>[c.id,c.length]));
  const headshunt=Number(options.headshunt??result?.parameters?.headshunt??100),locoLength=Number(options.locomotive??result?.parameters?.locomotive??20),left=28,geometry=YardModel.geometry(capacities,headshunt,left),{pxPerMetre,commonEnd}=geometry;
  const height=Math.max(245,n*62+82),width=Math.max(680,commonEnd+78);
  const diagram=element('div',`yard-diagram${options.editMode?' editable':''}`);diagram.style.height=`${height}px`;diagram.style.width=`${width}px`;diagram.dataset.scale=pxPerMetre;
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('aria-hidden','true');
  const path=(d,cls='yard-rail')=>{const p=document.createElementNS(ns,'path');p.setAttribute('d',d);p.setAttribute('class',cls);svg.append(p);};
  state.tracks.forEach((_,i)=>{const y=34+i*62,start=geometry.starts[i];path(`M ${start} ${y} L ${commonEnd} ${y}`);if(i+1<n)path(`M ${start} ${y} L ${geometry.starts[i+1]} ${34+(i+1)*62}`,'yard-rail ladder-link');path(`M ${commonEnd} ${y-12} L ${commonEnd} ${y+12}`,'yard-rail track-stop');});const longestIndex=capacities.indexOf(Math.max(...capacities)),headY=34+longestIndex*62;path(`M ${left} ${headY} L ${geometry.starts[longestIndex]} ${headY}`,'yard-rail headshunt-line');diagram.append(svg);
  const hsLabel=element('span','headshunt-label',`牵出线 ${headshunt}m`);hsLabel.style.left=`${left}px`;hsLabel.style.top=`${headY+10}px`;diagram.append(hsLabel);
  const consist=element('div','headshunt-consist');consist.style.left=`${left}px`;consist.style.top=`${headY-34}px`;const loco=element('span','loco','调机');loco.style.width=`${Math.max(28,locoLength*pxPerMetre)}px`;consist.append(loco);state.load.forEach(id=>consist.append(yardCar(id,destinations,lengths,pxPerMetre)));diagram.append(consist);
  state.tracks.forEach((ids,i)=>{const y=34+i*62,start=geometry.starts[i],zone=element('div','track-drop-zone');zone.dataset.trackIndex=i;zone.style.left=`${start}px`;zone.style.top=`${y-24}px`;zone.style.width=`${capacities[i]*pxPerMetre}px`;zone.style.height='48px';
    const label=element('span','track-name',roman(i+1));label.style.left=`${start-42}px`;label.style.top=`${y-25}px`;diagram.append(label);
    const row=element('div','track-consist');if(YardModel.occupied(ids,lengths)>capacities[i]+1e-8)row.classList.add('over-capacity');ids.forEach(id=>row.append(yardCar(id,destinations,lengths,pxPerMetre,options.editMode)));zone.append(row);
    if(options.allowTrackDelete){const remove=element('button','track-delete','×');remove.type='button';remove.title=`删除${roman(i+1)}道`;remove.setAttribute('aria-label',remove.title);remove.addEventListener('pointerdown',e=>e.stopPropagation());remove.addEventListener('click',e=>{e.stopPropagation();deleteTrack(i);});zone.append(remove);}diagram.append(zone);
  });
  container.append(diagram);
  if(options.scaleId&&$('#'+options.scaleId))$('#'+options.scaleId).textContent=`比例尺 1m = ${pxPerMetre.toFixed(2)}px`;
}
function renderInitialPreview(){
  if(!$('#initialPreview'))return;const shared={lengths:Object.fromEntries(cars.map(c=>[c.id,Number(c.length)||0])),headshunt:Number($('#headshunt')?.value)||0,locomotive:Number($('#locomotive')?.value)||0},destinations=Object.fromEntries(cars.map(c=>[c.id,c.destination])),position=Number($('#initialTrack')?.value)||1;
  renderYardInto($('#initialPreview'),{tracks:tracks.map(t=>splitIds(t.initialText)),load:[],position},tracks,destinations,{...shared,editMode:'initial',allowTrackDelete:true,scaleId:'yardScale'});
  renderYardInto($('#goalPreview'),{tracks:tracks.map(t=>splitIds(t.goalText)),load:[],position},tracks,destinations,{...shared,editMode:'goal',allowTrackDelete:false,scaleId:'goalYardScale'});
}
function renderYard(state){renderYardInto($('#yardView'),state,result.parameters.tracks,result.destinations);}
function startYardDrag(event){
  if(busy||event.button>0)return;event.preventDefault();const source=event.currentTarget,rect=source.getBoundingClientRect(),ghost=source.cloneNode(true);ghost.className='car drag-ghost';ghost.style.width=`${rect.width}px`;ghost.style.left=`${event.clientX-rect.width/2}px`;ghost.style.top=`${event.clientY-rect.height/2}px`;document.body.append(ghost);source.classList.add('dragging-source');document.body.classList.add('yard-dragging',`dragging-${source.dataset.editMode}`);
  yardDrag={id:source.dataset.carId,mode:source.dataset.editMode,source,ghost,pointerId:event.pointerId};source.setPointerCapture(event.pointerId);source.addEventListener('pointermove',moveYardDrag);source.addEventListener('pointerup',endYardDrag,{once:true});source.addEventListener('pointercancel',cancelYardDrag,{once:true});$(yardDrag.mode==='initial'?'#dragHint':'#goalDragHint').textContent=`正在移动车辆 ${yardDrag.id}`;
}
function moveYardDrag(event){if(!yardDrag)return;yardDrag.ghost.style.left=`${event.clientX-yardDrag.ghost.offsetWidth/2}px`;yardDrag.ghost.style.top=`${event.clientY-yardDrag.ghost.offsetHeight/2}px`;document.querySelectorAll('.track-drop-zone').forEach(z=>z.classList.remove('drop-target'));$('#vehicleTrash').classList.remove('drop-target');const at=document.elementFromPoint(event.clientX,event.clientY),zone=at?.closest?.('.track-drop-zone');if(zone?.closest('.yard-card')===yardDrag.source.closest('.yard-card'))zone.classList.add('drop-target');if(yardDrag.mode==='initial')at?.closest?.('.vehicle-trash')?.classList.add('drop-target');}
function dropIndex(zone,x,id){const items=[...zone.querySelectorAll('.draggable-car')].filter(e=>e.dataset.carId!==id);for(let i=0;i<items.length;i++){const r=items[i].getBoundingClientRect();if(x<r.left+r.width/2)return i;}return items.length;}
function finishYardDrag(){if(!yardDrag)return;yardDrag.ghost.remove();yardDrag.source.classList.remove('dragging-source');document.body.classList.remove('yard-dragging','dragging-initial','dragging-goal');document.querySelectorAll('.track-drop-zone').forEach(z=>z.classList.remove('drop-target'));$('#vehicleTrash').classList.remove('drop-target');yardDrag=null;}
function endYardDrag(event){
  if(!yardDrag)return;const id=yardDrag.id,mode=yardDrag.mode,hint=$(mode==='initial'?'#dragHint':'#goalDragHint'),at=document.elementFromPoint(event.clientX,event.clientY),trash=mode==='initial'?at?.closest?.('.vehicle-trash'):null,zone=at?.closest?.('.track-drop-zone'),sameDiagram=zone?.closest('.yard-card')===yardDrag.source.closest('.yard-card');
  if(trash){finishYardDrag();deleteVehicle(id);return;}
  if(zone&&sameDiagram){try{const field=mode==='initial'?'initialText':'goalText',rows=tracks.map(t=>splitIds(t[field])),lengths=Object.fromEntries(cars.map(c=>[c.id,Number(c.length)||0])),capacities=tracks.map(t=>Number(t.capacity)||0),next=YardModel.move(rows,id,Number(zone.dataset.trackIndex),dropIndex(zone,event.clientX,id),lengths,capacities);tracks.forEach((t,i)=>t[field]=next[i].join(','));renderEditors();invalidate();clearError();hint.textContent=`车辆 ${id} 已重新连挂`;renderInitialPreview();}catch(e){error(e.message);hint.textContent=e.message;}}
  else hint.textContent='未放入对应状态图的股道，位置保持不变';finishYardDrag();
}
function cancelYardDrag(){if(yardDrag)$(yardDrag.mode==='initial'?'#dragHint':'#goalDragHint').textContent='拖动已取消';finishYardDrag();}
function displayStep(value){
  if(!result)return;currentStep=Math.max(0,Math.min(value,result.operations.length));const op=result.operations[currentStep-1];
  $('#stepCounter').textContent=`${currentStep} / ${result.operations.length}`;$('#stepKicker').textContent=op?`第${op.step}步 · 第${op.round}轮`:'初始状态';
  $('#stepTitle').textContent=op?`${op.kind==='PULL'?'牵出 / 连挂':'推送 / 摘挂'} · ${op.trackName}道`:'作业开始';
  $('#actionDetail').textContent=op?`车辆 ${op.cars.join('、')}；机后 ${op.before} → ${op.after} 辆。本步 ${secs(op.durationSeconds)}，累计 ${secs(op.cumulativeSeconds)}。`:'左侧牵出线经咽喉连接各股道，右侧为股道尽端；股道编号和t变量从1开始。';
  $('#prevStep').disabled=currentStep===0;$('#nextStep').disabled=currentStep===result.operations.length;
  renderYard(op?op.state:result.initial);$('#sanDetail').classList.toggle('hidden',!op);
  if(op)renderModel(result.steps[op.round-1]);
  [...$('#timeline').children].forEach((li,i)=>{li.classList.toggle('active',i===currentStep);li.querySelector('button').setAttribute('aria-current',i===currentStep?'step':'false');});
}
function renderModel(round){
  const grid=element('div','san-model-grid'), summary=element('div'), arcs=element('div');
  summary.append(element('strong','',`第${round.step}轮 · t_${round.sourceTrack} = 1（${round.sourceTrackName}道）`));
  const g=result.objective==='time'?round.cumulativeSeconds:round.step;
  [`R = Σ rᵢⱼxᵢⱼ = ${round.reward.toFixed(4)}`,`T = ${secs(round.cumulativeSeconds)}；Λ = ${round.lambda}`,`w = ${result.weight}；wΛ = ${(result.weight*round.lambda).toFixed(2)}`,`η = ${g.toFixed(2)} + ${(result.weight*round.lambda).toFixed(2)} = ${round.priority.toFixed(2)}`,`选中弧 ${round.model.selectedArcs.length} / 候选弧 ${round.model.candidateArcs.length}`,Object.entries(round.model.t).map(([k,v])=>`${k}=${v}`).join('，')].forEach(t=>summary.append(element('p','',t)));
  arcs.append(element('strong','','选中弧与对应奖励'));
  round.model.selectedArcs.forEach(a=>arcs.append(element('span','arc-chip',`${a.name}: ${a.from} → ${a.to} · ${a.type} · r=${Number(a.reward.toFixed(4))}`)));
  grid.append(summary,arcs);$('#sanModel').replaceChildren(grid);
}
function download(){if(!result)return;const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=element('a');a.href=url;a.download='shunting-solution-v3.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('#loadExample').addEventListener('click',()=>applyConfig(defaults));
$('#addCar').addEventListener('click',()=>{cars.push({id:nextCarId(),destination:'1',length:15});renderEditors();invalidate();renderInitialPreview();});
function addTrack(){tracks.push({name:roman(tracks.length+1),capacity:120,initialText:'',goalText:''});renderEditors();invalidate();renderInitialPreview();}
$('#addTrack').addEventListener('click',addTrack);$('#quickAddTrack').addEventListener('click',addTrack);
fields.forEach(id=>$('#'+id).addEventListener('input',()=>{invalidate();updateFormula();renderInitialPreview();}));
document.querySelectorAll('.nav-tab').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
document.querySelectorAll('.next-view').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.target)));
$('#solveButton').addEventListener('click',runSolver);$('#downloadResult').addEventListener('click',download);
$('#prevStep').addEventListener('click',()=>displayStep(currentStep-1));$('#nextStep').addEventListener('click',()=>displayStep(currentStep+1));
applyConfig(defaults);
if(document.modelContext?.registerTool){
  const controller=new AbortController();window.addEventListener('pagehide',()=>controller.abort(),{once:true});
  const schema={type:'object',additionalProperties:false,properties:{
    cars:{type:'array',items:{type:'object',properties:{id:{type:'string'},destination:{type:'string'},length:{type:'number'}},required:['id','destination','length']}},
    tracks:{type:'array',items:{type:'object',properties:{name:{type:'string'},capacity:{type:'number'},initial:{type:'array',items:{type:'string'}},goal:{type:'array',items:{type:'string'}}},required:['name','capacity','initial','goal']}},
    headshunt:{type:'number'},locomotive:{type:'number'},timeLimit:{type:'number'},weight:{type:'number'},poolSize:{type:'integer'},initialTrack:{type:'integer'},objective:{enum:['time','rounds'],type:'string'}},required:['cars','tracks','headshunt','locomotive']};
  for(const tool of [
    {name:'configure_shunting_problem',description:'校验后替换页面调车配置。w显式可调；初始股道编号从1开始。',inputSchema:schema,annotations:{readOnlyHint:false,untrustedContentHint:false},execute(input){if(busy)throw Error('求解中不能修改配置');applyConfig(input);return {configured:true,weight:Number($('#weight').value)};}},
    {name:'solve_shunting_problem',description:'求解当前配置并显示全程调车时间、SAN模型与逐步过程。',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},async execute(){const r=await runSolver();if(!r)throw Error($('#validation').textContent);return {status:r.status,totalTimeSeconds:r.totalTimeSeconds,weight:r.weight,roundCount:r.roundCount,operationCount:r.operationCount};}}
  ])try{Promise.resolve(document.modelContext.registerTool(tool,{signal:controller.signal})).catch(()=>{});}catch(_){}
}
