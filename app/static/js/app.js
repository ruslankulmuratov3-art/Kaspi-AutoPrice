(function(){
  const qs = (s, root=document) => root.querySelector(s);
  const qsa = (s, root=document) => [...root.querySelectorAll(s)];

  qsa('.reveal').forEach((el, i) => {
    el.style.animationDelay = `${Math.min(i * 45, 320)}ms`;
  });

  qsa('form').forEach(form => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('button[type="submit"], button:not([type])');
      if(!btn || btn.dataset.noLoading) return;
      const oldText = btn.innerHTML;
      btn.dataset.oldText = oldText;
      btn.innerHTML = '<span class="spinner"></span> Подготавливаю...';
      btn.disabled = true;
      if(form.hasAttribute('data-download-form')){
        setTimeout(() => {
          btn.innerHTML = oldText;
          btn.disabled = false;
        }, 180000);
      }
    });
  });

  qsa('input[type="file"]').forEach(input => {
    input.addEventListener('change', () => {
      const label = input.closest('label');
      if(label && input.files && input.files[0]){
        label.dataset.fileName = input.files[0].name;
      }
    });
  });

  qsa('[data-accordion]').forEach(trigger => {
    trigger.addEventListener('click', () => {
      const wrap = trigger.closest('.accordion');
      if(wrap) wrap.classList.toggle('open');
    });
  });

  qsa('[data-open-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const panel = qs('#' + btn.dataset.openPanel);
      if(panel){
        qsa('.drawer-panel.open').forEach(p => p.classList.remove('open'));
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
      }
    });
  });

  qsa('[data-close-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const panel = btn.closest('.drawer-panel');
      if(panel){
        panel.classList.remove('open');
        panel.setAttribute('aria-hidden', 'true');
      }
    });
  });

  const mobileMenu = qs('.mobile-menu');
  const sidebar = qs('.sidebar');
  if(mobileMenu && sidebar){
    mobileMenu.addEventListener('click', () => sidebar.classList.toggle('open'));
    qsa('.nav-link').forEach(link => link.addEventListener('click', () => sidebar.classList.remove('open')));
  }

  qsa('.transition-link').forEach(link => {
    link.addEventListener('click', () => {
      document.body.classList.add('page-fade-out');
    });
  });

  const selectedIds = () => qsa('.product-check:checked').map(input => input.value).filter(Boolean);
  const updateSelectedCount = () => {
    const count = selectedIds().length;
    qsa('[data-selected-count]').forEach(el => { el.textContent = String(count); });
    const all = qs('[data-select-all]');
    const checks = qsa('.product-check');
    if(all && checks.length){
      all.checked = count === checks.length;
      all.indeterminate = count > 0 && count < checks.length;
    }
  };

  qsa('.product-check').forEach(input => input.addEventListener('change', updateSelectedCount));

  const selectAll = qs('[data-select-all]');
  if(selectAll){
    selectAll.addEventListener('change', () => {
      qsa('.product-check').forEach(input => { input.checked = selectAll.checked; });
      updateSelectedCount();
    });
  }

  const clearSelection = qs('[data-clear-selection]');
  if(clearSelection){
    clearSelection.addEventListener('click', () => {
      qsa('.product-check').forEach(input => { input.checked = false; });
      updateSelectedCount();
    });
  }

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
      const selected = selectedIds().length;
      const limitInput = qs('input[name="limit_count"]', form);
      const limit = limitInput ? parseInt(limitInput.value || '0', 10) : 0;
      const planned = selected || limit || 0;
      let p = 0;
      panel.hidden = false;
      if(textEl){
        textEl.textContent = planned > 0
          ? `Стартовал расчёт: ${planned} товар(ов). Не закрывай страницу, файл скачается автоматически.`
          : 'Стартовал расчёт всех готовых товаров. Это может занять долго, не закрывай страницу.';
      }
      if(window.kaspiProgressTimer) clearInterval(window.kaspiProgressTimer);
      const maxBeforeDownload = 96;
      const tickMs = planned > 500 ? 1800 : planned > 100 ? 1200 : 700;
      window.kaspiProgressTimer = setInterval(() => {
        const slowDown = p > 82 ? 0.35 : p > 60 ? 0.65 : 1;
        p = Math.min(maxBeforeDownload, p + Math.max(1, Math.round((Math.random() * 4 + 1) * slowDown)));
        if(bar) bar.style.width = p + '%';
        if(percentEl) percentEl.textContent = p + '%';
        if(textEl && p > 90) textEl.textContent = 'Почти готово. Если товаров много, последние проценты могут идти дольше.';
      }, tickMs);
    });
  });

  updateSelectedCount();

})();

if ('serviceWorker' in navigator) { window.addEventListener('load', () => navigator.serviceWorker.register('/static/service-worker.js').catch(() => {})); }
