const $ = id => document.getElementById(id);
let scanId = null, scanData = null, polling = false;
const downloadJobs = new Map();
function safeURL(value) { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : null; } catch { return null; } }
function element(tag, cls, text) { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; }
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: {'Content-Type':'application/json'}, body:JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Confira os campos e tente novamente.');
  return data;
}
function renderVideos() {
  if (!scanData) return;
  const grid = $('videos'); grid.replaceChildren();
  const query = $('search').value.toLowerCase();
  const videos = scanData.videos.filter(v => (v.title+' '+v.url+' '+v.page).toLowerCase().includes(query));
  $('count').textContent = scanData.videos.length;
  if (!videos.length) { const box = element('div','empty'); box.append(element('div','empty-icon','▷'), element('h3','',query ? 'Nenhum resultado para este filtro' : scanData.status === 'running' ? 'Procurando vídeos…' : 'Nenhum vídeo encontrado'),element('p','', 'Se o vídeo aparece após um quiz ou login, marque “Abrir página e aguardar o vídeo” nas opções acima, ou cole o código do player já carregado.')); grid.append(box); }
  for (const video of videos) {
    const card = element('article','video-card'), poster = element('div','poster');
    poster.append(element('span','','▷'));
    const thumbnail = safeURL(video.thumbnail);
    if (thumbnail) { const img = element('img'); img.src = thumbnail; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.addEventListener('error',()=>img.remove()); poster.append(img); }
    poster.append(element('span','format',(video.extension || (video.kind === 'direct' ? 'MÍDIA DIRETA' : 'PLAYER')).toUpperCase()));
    const content = element('div','card-content'); content.append(element('h3','',video.title));
    const meta = element('p'); let host = ''; try {host = new URL(video.url).hostname;} catch {}
    meta.append(document.createTextNode((video.identity_verified ? '✓ Reprodução visível validada · ' : '') + host + (video.duration ? ` · ${Math.floor(video.duration/60)}:${String(Math.floor(video.duration%60)).padStart(2,'0')}` : '') + ' · '));
    const link = element('a','','Abrir origem'); link.href = safeURL(video.page) || '#'; link.target = '_blank'; link.rel = 'noopener noreferrer'; meta.append(link); content.append(meta);
    const actions = element('div','card-actions');
    for (const [mode,text,cls] of [['video','↓ Baixar vídeo','primary'],['audio','♫ Áudio MP3','secondary']]) { const button = element('button',cls,text); button.type = 'button'; const owningScan = scanId; button.onclick = async () => { button.disabled = true; try { const job = await api('/api/downloads',{scan_id:owningScan,video_id:video.id,mode}); downloadJobs.set(job.id,{id:job.id,title:video.title,mode,status:'queued'}); renderDownloads(); pollDownload(job.id); } catch(e) {notice(e.message);} finally {button.disabled = false;} }; actions.append(button); }
    content.append(actions); card.append(poster,content); grid.append(card);
  }
}
function renderScan() {
  renderVideos();
  const running = scanData.status === 'running';
  $('scan-button').disabled = running; $('cancel-button').hidden = !running;
  if (!running) { $('cancel-button').disabled = false; $('cancel-button').textContent = 'Parar'; }
  const browser = scanData.browser;
  const phase = browser?.players ? `Player encontrado · ${browser.manifests} manifesto(s) recebido(s): ` : scanData.phase === 'interactive' ? 'Responda ao quiz na janela de análise até o vídeo aparecer (até 10 min): ' : scanData.phase === 'rendering' ? 'Carregando página com JavaScript: ' : 'Analisando: ';
  $('current').hidden = !running; $('current').textContent = phase + (scanData.current || 'Preparando varredura…');
  const status = {running:'Varredura em andamento',done:'Varredura concluída',cancelled:'Varredura interrompida',error:'Falha na varredura'}[scanData.status];
  $('scan-status').textContent = `${status} · ${scanData.visited} página(s) analisada(s)` + (scanData.limited ? ' · Limite de páginas ou profundidade atingido.' : '');
  $('warnings').hidden = !scanData.warnings.length;
  $('warning-list').replaceChildren(...scanData.warnings.map(w=>element('li','',w.page+' — '+w.message)));
  if (scanData.error) notice(scanData.error);
}
async function pollScan() {
  if (polling) return; polling = true;
  try { while (scanId) { scanData = await api('/api/scans/'+scanId); renderScan(); if (scanData.status !== 'running') break; await new Promise(r=>setTimeout(r,1400)); } }
  catch(e) { notice('Não foi possível acompanhar a varredura: '+e.message); $('scan-button').disabled = false; $('cancel-button').hidden = true; }
  finally {polling = false;}
}
function renderDownloads() {
  const list = $('download-list'); list.replaceChildren();
  for (const job of [...downloadJobs.values()].reverse()) {
    const row = element('div','download-row'), content = element('div'); content.append(element('strong','',job.title));
    const labels = {queued:'Na fila',downloading:'Baixando',processing:'Processando arquivo',done:'Pronto para salvar',error:'Erro'};
    content.append(element('p','',`${job.mode === 'audio' ? 'Áudio MP3' : 'Vídeo'} · ${labels[job.status] || job.status}`+(job.error ? ' — '+job.error : '')+(job.status==='downloading' && job.progress!=null ? ' · '+job.progress+'%' : '')));
    if (!['done','error'].includes(job.status)) {const bar = element('progress'); bar.max=100; if (job.progress!=null) bar.value=job.progress; content.append(bar);}
    row.append(content);
    if(job.status==='done') {const link = element('a','','↓ Salvar arquivo');link.href='/api/downloads/'+job.id+'/file';link.download=job.filename;row.append(link);}
    list.append(row);
  }
}
async function pollDownload(id) {
  try { while(true) { const job=await api('/api/downloads/'+id); downloadJobs.set(id,job);renderDownloads(); if(['done','error'].includes(job.status))break; await new Promise(r=>setTimeout(r,1200)); } }
  catch(e) { const job=downloadJobs.get(id);job.status='error';job.error=e.message;renderDownloads(); }
}
$('scan-form').addEventListener('submit', async event => {event.preventDefault();notice('');$('scan-button').disabled=true;try {const result=await api('/api/scans',{url:$('url').value,domain:$('domain').value,scope:document.querySelector('[name=scope]:checked').value,max_pages:Number($('max-pages').value),max_depth:Number($('max-depth').value),render_js:$('render-js').checked,interactive:$('interactive').checked,player_code:$('player-code').value});scanId=result.id;pollScan();}catch(e){notice(e.message);$('scan-button').disabled=false;}});
$('cancel-button').onclick = async () => {try {await api('/api/scans/'+scanId+'/cancel',{});$('cancel-button').disabled=true;$('cancel-button').textContent='Parando…';}catch(e){notice(e.message);}};
$('search').addEventListener('input',renderVideos);
document.querySelectorAll('[name=scope]').forEach(input=>input.addEventListener('change',()=>{document.querySelector('.limits').hidden=document.querySelector('[name=scope]:checked').value==='page';}));
document.querySelector('.limits').hidden=true;
