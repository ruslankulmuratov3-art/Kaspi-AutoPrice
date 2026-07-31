(function(){
  const body=document.body;
  const token=body.dataset.helperToken;
  if(!token)return;
  const q=(s)=>document.querySelector(s);
  const consent=q('[data-helper-consent]');
  const runner=q('[data-helper-runner]');
  const fallback=q('[data-helper-fallback]');
  const start=q('[data-helper-start]');
  const cancel=q('[data-helper-cancel]');
  const stop=q('[data-helper-stop]');
  const log=q('[data-helper-log]');
  let stopped=false;
  let wakeLock=null;
  let info=null;
  const wait=(ms)=>new Promise(r=>setTimeout(r,ms));
  const set=(s,v)=>{const n=q(s);if(n)n.textContent=String(v)};
  const api=(path,options={})=>fetch(`/api/helper/${encodeURIComponent(token)}${path}`,{headers:{'Content-Type':'application/json','Accept':'application/json',...(options.headers||{})},...options});
  const write=(text)=>{if(log)log.textContent=text};
  const updateNetwork=()=>set('[data-helper-network]',navigator.onLine?(navigator.connection?.effectiveType||'онлайн'):'нет сети');
  addEventListener('online',updateNetwork);addEventListener('offline',updateNetwork);updateNetwork();

  async function requestWakeLock(){try{if('wakeLock'in navigator)wakeLock=await navigator.wakeLock.request('screen')}catch(e){}}
  async function fetchInfo(){const r=await api('/info');if(!r.ok)throw new Error(await r.text());info=await r.json();return info}
  async function consentToRun(){const r=await api('/consent',{method:'POST',body:JSON.stringify({consent:true})});if(!r.ok)throw new Error(await r.text())}
  async function testCors(task){
    const url=`${info.offers_base_url}/${task.public_product_id}`;
    const payload={cityId:info.city_id,id:String(task.public_product_id),page:0,limit:info.offers_limit,sortOption:info.sort_option};
    const options={method:(info.method||'POST').toUpperCase(),headers:{'Accept':'application/json, text/plain, */*','Content-Type':'application/json'}};
    if(options.method==='GET')return fetch(url+'?'+new URLSearchParams(payload),options);
    options.body=JSON.stringify(payload);return fetch(url,options);
  }
  async function sendResult(task,status,payload,httpStatus,error,retry){
    const r=await api('/result',{method:'POST',body:JSON.stringify({product_id:task.product_id,public_product_id:String(task.public_product_id),lease_token:task.lease_token,status,payload,http_status:httpStatus,error:error||'',retry_after_seconds:retry||null})});
    if(!r.ok)throw new Error(await r.text());return r.json();
  }
  function updateProgress(completed,success,errors,total){
    set('[data-helper-completed]',completed);set('[data-helper-success]',success);set('[data-helper-errors]',errors);
    const pct=total?Math.min(100,Math.round(completed/total*100)):0;set('[data-helper-percent]',pct+'%');
    const bar=q('[data-helper-bar]');if(bar)bar.style.width=pct+'%';set('[data-helper-left]',total?Math.max(0,total-completed):'—');
  }
  async function run(){
    stopped=false;consent.hidden=true;runner.hidden=false;fallback.hidden=true;await requestWakeLock();
    try{await fetchInfo();await consentToRun()}catch(e){write('Не удалось открыть сессию: '+e.message);return}
    let completed=info.completed||0,success=info.success||0,errors=info.errors||0,total=info.ready_count||0;
    updateProgress(completed,success,errors,total);set('[data-helper-state]','Работает');
    let first=true;
    while(!stopped){
      if(!navigator.onLine){write('Нет сети. Продолжим после восстановления…');await wait(3000);continue}
      let batch;
      try{const r=await api(`/tasks?limit=${Math.min(info.batch_size||10,10)}`);if(!r.ok)throw new Error(await r.text());batch=await r.json()}catch(e){write('Render временно недоступен. Повторяем…');await wait(5000);continue}
      if(!batch.items?.length){
        try{await api('/complete',{method:'POST'});write('Готово. Цены рассчитаны, полный XML обновлён.');set('[data-helper-state]','Готово');updateProgress(total,success,errors,total)}catch(e){write('Проверка завершена, XML будет обновлён сервером.')}
        break;
      }
      for(const task of batch.items){
        if(stopped)break;
        write(`Проверяем: ${task.name||task.sku}`);
        let response;
        try{response=await testCors(task)}catch(e){
          if(first){runner.hidden=true;fallback.hidden=false;set('[data-helper-state]','CORS');return}
          errors++;completed++;await sendResult(task,'error',null,null,'Браузер заблокировал прямой запрос (CORS)',3600).catch(()=>{});updateProgress(completed,success,errors,total);continue
        }
        first=false;
        if(response.status===429||response.status===403||response.status===405){
          errors++;completed++;await sendResult(task,'error',null,response.status,`Kaspi вернул HTTP ${response.status}`,3600).catch(()=>{});updateProgress(completed,success,errors,total);write('Kaspi временно ограничил запросы. Проверка остановлена безопасно.');stopped=true;break
        }
        try{
          const payload=await response.json();const result=await sendResult(task,'ok',payload,response.status,'',null);completed++;success++;updateProgress(completed,success,errors,total);
          const pricing=result.pricing||{};write(pricing.changed?`Цена рассчитана: ${pricing.old_price} → ${pricing.new_price} ₸`:`Без изменения: ${pricing.reason||'цена безопасна'}`)
        }catch(e){errors++;completed++;await sendResult(task,'error',null,response.status,'Некорректный ответ Kaspi',1800).catch(()=>{});updateProgress(completed,success,errors,total)}
        await wait(4000);
      }
    }
    try{wakeLock?.release()}catch(e){}
  }
  start?.addEventListener('click',run);
  cancel?.addEventListener('click',async()=>{await api('/consent',{method:'POST',body:JSON.stringify({consent:false})}).catch(()=>{});location.href='about:blank'});
  stop?.addEventListener('click',async()=>{stopped=true;await api('/stop',{method:'POST'}).catch(()=>{});write('Проверка остановлена.');set('[data-helper-state]','Остановлено')});
})();
