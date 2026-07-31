(function(){
  const qs=(selector,root=document)=>root.querySelector(selector);
  const qsa=(selector,root=document)=>[...root.querySelectorAll(selector)];
  const menuBtn=qs('.mobile-menu');
  const overlay=qs('[data-menu-overlay]');
  const closeMenu=()=>document.body.classList.remove('menu-open');
  menuBtn?.addEventListener('click',()=>document.body.classList.toggle('menu-open'));
  overlay?.addEventListener('click',closeMenu);
  qsa('.nav-link').forEach(link=>{
    const href=link.getAttribute('href')||'';
    if(href!=='/'&&location.pathname.startsWith(href))link.classList.add('active');
    link.addEventListener('click',closeMenu);
  });

  const toast=(text)=>{
    const stack=qs('.toast-stack');
    if(!stack)return;
    const node=document.createElement('div');
    node.className='toast-pop';
    node.textContent=text;
    stack.appendChild(node);
    setTimeout(()=>node.remove(),2600);
  };
  qsa('.toast-auto').forEach(node=>setTimeout(()=>{
    node.style.opacity='0';
    setTimeout(()=>node.remove(),250);
  },5200));

  qsa('form').forEach(form=>form.addEventListener('submit',()=>{
    const button=form.querySelector('button[type="submit"],button:not([type])');
    if(!button||button.dataset.noLoading)return;
    button.dataset.oldText=button.innerHTML;
    button.innerHTML='<span class="spinner"></span> Работаю…';
    button.disabled=true;
    setTimeout(()=>{
      button.innerHTML=button.dataset.oldText||'Готово';
      button.disabled=false;
    },20000);
  }));
  qsa('[data-accordion]').forEach(trigger=>trigger.addEventListener('click',()=>trigger.closest('.accordion')?.classList.toggle('open')));
  qsa('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{
    const direct=button.closest('[data-copy-text]')?.dataset.copyText||'';
    const target=button.dataset.copy?qs(button.dataset.copy):null;
    const text=direct||target?.textContent.trim()||'';
    try{await navigator.clipboard.writeText(text);toast('Скопировано')}catch{toast('Скопируйте вручную')}
  }));

  const selectedIds=()=>qsa('.product-check:checked').map(input=>input.value).filter(Boolean);
  const updateSelected=()=>{
    const count=selectedIds().length;
    qsa('[data-selected-count]').forEach(node=>node.textContent=String(count));
    const all=qs('[data-select-all]');
    const checks=qsa('.product-check');
    if(all&&checks.length){
      all.checked=count===checks.length;
      all.indeterminate=count>0&&count<checks.length;
    }
  };
  qsa('.product-check').forEach(input=>input.addEventListener('change',updateSelected));
  qs('[data-select-all]')?.addEventListener('change',event=>{
    qsa('.product-check').forEach(input=>input.checked=event.target.checked);
    updateSelected();
  });
  qsa('.selection-aware-form').forEach(form=>form.addEventListener('submit',()=>{
    qsa('input[data-generated-selection]',form).forEach(node=>node.remove());
    selectedIds().forEach(id=>{
      const hidden=document.createElement('input');
      hidden.type='hidden';
      hidden.name='product_ids';
      hidden.value=id;
      hidden.dataset.generatedSelection='1';
      form.appendChild(hidden);
    });
  }));
  updateSelected();

  const formatDuration=(seconds)=>{
    if(!Number.isFinite(seconds)||seconds<0)return 'Время уточняется…';
    seconds=Math.max(0,Math.round(seconds));
    if(seconds<45)return `Примерно ${Math.max(1,seconds)} сек.`;
    const minutes=Math.round(seconds/60);
    if(minutes<60)return `Примерно ${minutes} мин.`;
    const hours=Math.floor(minutes/60);
    const rest=minutes%60;
    return `Примерно ${hours} ч${rest?` ${rest} мин`:''}`;
  };

  const dashboard=qs('[data-live-dashboard]');
  if(dashboard){
    const storeId=dashboard.dataset.storeId;
    const setText=(selector,value)=>{
      const node=qs(selector);
      if(node&&value!==undefined&&value!==null)node.textContent=String(value);
    };
    let lastProcessed=null;
    let lastSampleAt=null;
    let smoothedRate=null;
    let lastMovementAt=Date.now();

    const refresh=async()=>{
      if(!storeId||storeId==='0')return;
      try{
        const response=await fetch(`/automation/status?store_id=${encodeURIComponent(storeId)}`,{headers:{Accept:'application/json'}});
        if(!response.ok)return;
        const data=await response.json();
        const job=data.job||{};
        const source=data.competitors||{};
        const feed=data.feed||{};
        const now=Date.now();
        const processed=Number(job.processed_now||0);
        const total=Number(job.total||0);
        const remaining=Number(job.remaining??Math.max(0,total-processed));

        if(lastProcessed!==null&&lastSampleAt!==null&&processed>lastProcessed){
          const seconds=(now-lastSampleAt)/1000;
          if(seconds>0){
            const rate=(processed-lastProcessed)/seconds;
            smoothedRate=smoothedRate===null?rate:(smoothedRate*0.65+rate*0.35);
            lastMovementAt=now;
          }
        }
        lastProcessed=processed;
        lastSampleAt=now;

        setText('[data-job-processed]',processed);
        setText('[data-job-total]',total);
        setText('[data-job-remaining]',remaining);
        setText('[data-job-percent]',`${job.percent||0}%`);
        setText('[data-job-changed]',job.changed||0);
        setText('[data-job-unchanged]',job.unchanged||0);
        setText('[data-job-skipped]',job.skipped||0);
        setText('[data-job-queued]',job.queued||0);
        setText('[data-job-errors]',job.errors||0);

        const queueCard=qs('[data-job-queued-card]');
        if(queueCard)queueCard.hidden=Number(job.queued||0)<=0;

        const bar=qs('[data-job-bar]');
        if(bar)bar.style.width=`${job.percent||0}%`;
        const badge=qs('[data-job-badge]');
        if(badge){
          const labels={queued:'В очереди',running:'Работает',paused:'Пауза',done:'Готово',error:'Ошибка',cancelled:'Отменено'};
          badge.textContent=labels[job.status]||'Ожидает';
          badge.classList.toggle('on',job.status==='running');
          badge.classList.toggle('pulse',job.status==='running');
          badge.classList.toggle('hot',['error','cancelled'].includes(job.status));
        }

        const etaNode=qs('[data-job-eta]');
        if(etaNode){
          if(job.status==='done'){
            etaNode.textContent='Расчёт завершён';
          }else if(job.status==='error'){
            etaNode.textContent='Остановлено из-за ошибки';
          }else if(job.status==='paused'){
            etaNode.textContent='Расчёт на паузе';
          }else if(['running','queued'].includes(job.status)&&remaining>0){
            const noMovementFor=(now-lastMovementAt)/1000;
            if(job.status==='running'&&noMovementFor>45&&processed>0){
              etaNode.textContent='Ожидание данных или завершения XML…';
            }else{
              const eta=smoothedRate&&smoothedRate>0?remaining/smoothedRate:Number(job.eta_seconds||0);
              etaNode.textContent=formatDuration(eta);
            }
          }else{
            etaNode.textContent='Ожидание запуска';
          }
        }

        setText('[data-metric="feed-products"]',feed.product_count||0);
        setText('[data-metric="snapshots"]',data.fresh_snapshots||0);
        setText('[data-metric="source-state"]',source.state==='open'?'пауза':'доступны');
        setText('[data-metric="source-note"]',source.cooldown_until?`до ${source.cooldown_until}`:'кэш активен');
      }catch(error){
        console.debug('status polling paused',error);
      }
    };
    refresh();
    setInterval(refresh,3000);
  }
})();
if('serviceWorker'in navigator){
  window.addEventListener('load',()=>navigator.serviceWorker.register('/static/service-worker.js').catch(()=>{}));
}
