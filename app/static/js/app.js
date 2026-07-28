(function(){
  const qs = (s, root=document) => root.querySelector(s);
  const qsa = (s, root=document) => [...root.querySelectorAll(s)];

  const menuBtn = qs('.mobile-menu');
  if(menuBtn){
    menuBtn.addEventListener('click', () => document.body.classList.toggle('menu-open'));
    qsa('.nav-link').forEach(a => a.addEventListener('click', () => document.body.classList.remove('menu-open')));
  }

  const path = window.location.pathname;
  qsa('.nav-link').forEach(a => {
    const href = a.getAttribute('href') || '';
    if(href !== '/' && path.startsWith(href)) a.classList.add('active');
  });

  qsa('form').forEach(form => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('button[type="submit"], button:not([type])');
      if(!btn || btn.dataset.noLoading) return;
      const old = btn.innerHTML;
      btn.dataset.oldText = old;
      btn.innerHTML = '<span class="spinner"></span> Работаю…';
      btn.disabled = true;
      if(form.hasAttribute('data-download-form')){
        setTimeout(() => { btn.innerHTML = old; btn.disabled = false; }, 180000);
      } else {
        setTimeout(() => { btn.innerHTML = old; btn.disabled = false; }, 15000);
      }
    });
  });

  qsa('[data-accordion]').forEach(trigger => {
    trigger.addEventListener('click', () => trigger.closest('.accordion')?.classList.toggle('open'));
  });

  qsa('[data-open-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const panel = qs('#' + btn.dataset.openPanel);
      if(!panel) return;
      qsa('.drawer-panel.open').forEach(p => p.classList.remove('open'));
      panel.classList.add('open');
      panel.setAttribute('aria-hidden','false');
    });
  });

  qsa('[data-close-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const panel = btn.closest('.drawer-panel');
      panel?.classList.remove('open');
      panel?.setAttribute('aria-hidden','true');
    });
  });

  qsa('[data-copy]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const target = qs(btn.dataset.copy);
      const text = target ? target.textContent.trim() : '';
      try{
        await navigator.clipboard.writeText(text);
        const old = btn.textContent;
        btn.textContent = 'Скопировано';
        setTimeout(() => btn.textContent = old, 1400);
      }catch(e){
        alert('Скопируй ссылку вручную.');
      }
    });
  });

  const selectedIds = () => qsa('.product-check:checked').map(input => input.value).filter(Boolean);
  const updateSelectedCount = () => {
    const count = selectedIds().length;
    qsa('[data-selected-count]').forEach(el => el.textContent = String(count));
    const all = qs('[data-select-all]');
    const checks = qsa('.product-check');
    if(all && checks.length){
      all.checked = count === checks.length;
      all.indeterminate = count > 0 && count < checks.length;
    }
  };

  qsa('.product-check').forEach(input => input.addEventListener('change', updateSelectedCount));
  qs('[data-select-all]')?.addEventListener('change', e => {
    qsa('.product-check').forEach(input => input.checked = e.target.checked);
    updateSelectedCount();
  });
  qs('[data-clear-selection]')?.addEventListener('click', () => {
    qsa('.product-check').forEach(input => input.checked = false);
    updateSelectedCount();
  });

  qsa('.selection-aware-form').forEach(form => {
    form.addEventListener('submit', () => {
      qsa('input[data-generated-selection]', form).forEach(el => el.remove());
      selectedIds().forEach(id => {
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'product_ids';
        hidden.value = id;
        hidden.dataset.generatedSelection = '1';
        form.appendChild(hidden);
      });
    });
  });

  qsa('[data-download-form]').forEach(form => {
    form.addEventListener('submit', () => {
      const panel = qs('[data-progress-panel]');
      if(!panel) return;
      const bar = qs('[data-progress-bar]', panel);
      const percentEl = qs('[data-progress-percent]', panel);
      const textEl = qs('[data-progress-text]', panel);
      let p = 0;
      panel.hidden = false;
      if(textEl) textEl.textContent = 'Расчёт запущен. Файл скачается автоматически.';
      clearInterval(window.kaspiProgressTimer);
      window.kaspiProgressTimer = setInterval(() => {
        p = Math.min(96, p + (p > 80 ? 1 : 4));
        if(bar) bar.style.width = p + '%';
        if(percentEl) percentEl.textContent = p + '%';
      }, 900);
    });
  });

  updateSelectedCount();
})();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/static/service-worker.js').catch(() => {}));
}
