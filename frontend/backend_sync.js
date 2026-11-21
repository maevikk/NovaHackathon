/* PROCESS LAB / minimal local-Python integration; original app.js remains untouched.
   If served by Live Server/file://, the website continues running the JS demo.
   If served by python -m backend.server, the 'Симуляция' button calls Python API.
*/
(() => {
  'use strict';
  const originalRun = runModel;
  const indicator = document.querySelector('.demo-pill');
  let backendAvailable = false;
  let inProgress = false;

  function setStatus(text) {
    if (indicator) {
      const dot = indicator.querySelector('.demo-dot');
      indicator.replaceChildren();
      if (dot) indicator.appendChild(dot);
      indicator.appendChild(document.createTextNode(text));
    }
  }

  async function endpoint(route, payload, timeout = 15000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(route, {
        method: payload ? 'POST' : 'GET',
        headers: payload ? {'Content-Type': 'application/json'} : {},
        body: payload ? JSON.stringify(payload) : undefined,
        signal: controller.signal,
        cache: 'no-store'
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const err = new Error(data?.message || `HTTP ${response.status}`);
        err.isValidation = response.status === 422;
        throw err;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  async function init() {
    try {
      const data = await endpoint('/api/v1/health', null, 1800);
      if (data?.status !== 'ok') return;
      backendAvailable = true;
      setStatus('Python API · локальная демо-модель');
    } catch (_) {
      setStatus('JavaScript · автономный демо-режим');
    }
  }

  // runModel вызывается тем же обработчиком кнопки из app.js.
  // Остальные сохранённые сценарии рассчитываются JS-движком по тем же формулам.
  runModel = async function runWithBackend(switchView = true) {
    if (!backendAvailable) return originalRun(switchView);
    if (inProgress) return false;
    inProgress = true;
    const snapshot = JSON.stringify(state.model);
    try {
      // Не применяем ответ, если пользователь поменял модель во время запроса.
      const result = await endpoint('/api/v1/simulate', {model: state.model});
      if (JSON.stringify(state.model) !== snapshot) {
        toast('Параметры изменились во время расчёта — запустите симуляцию ещё раз');
        return false;
      }
      state.result = result;
      state.baselineResult = state.baselineResult || simulate(state.base);
      state.dirty = false;
      state.analysisNotice = '';
      state.lastRunAt = Date.now();
      if (switchView) state.currentView = 'simulation';
      draw();
      toast(`Python API: рассчитано ${result.accepted} из ${result.target} изделий`);
      return true;
    } catch (error) {
      if (error.isValidation) {
        state.analysisNotice = error.message;
        state.dirty = true;
        draw();
        toast('Ошибка входных данных: ' + error.message);
        return false;
      }
      backendAvailable = false;
      setStatus('JavaScript · резервный демо-режим');
      toast('Сервер недоступен — расчёт продолжен в браузере');
      return originalRun(switchView);
    } finally {
      inProgress = false;
    }
  };

  init();
})();
