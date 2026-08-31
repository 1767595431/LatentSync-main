/**
 * UI motion — wraps local animate.css (MIT, animate.style v4.1.1).
 * No external CDN; all assets under /admin/assets/vendor/.
 */
(function (global) {
  const reduced = () => global.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function nextFrame() {
    return new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    });
  }

  function stripAnimate(el) {
    if (!el) return;
    [...el.classList].forEach((c) => {
      if (c === 'animate__animated' || c.startsWith('animate__')) el.classList.remove(c);
    });
    el.style.removeProperty('--animate-duration');
    el.style.removeProperty('--animate-delay');
  }

  const FX = {
    silent: false,
    _silentDepth: 0,

    setSilent(on) {
      if (on) this._silentDepth += 1;
      else this._silentDepth = Math.max(0, this._silentDepth - 1);
      this.silent = this._silentDepth > 0;
      document.documentElement.classList.toggle('fx-silent', this.silent);
    },

    shouldAnimate(prefer = true) {
      return !!prefer && !this.silent && !reduced();
    },

    run(el, name, duration = 400, delay = 0, opts = {}) {
      if (!el || !this.shouldAnimate(true)) return Promise.resolve();
      const persist = !!opts.persist;
      return new Promise((resolve) => {
        stripAnimate(el);
        el.style.setProperty('--animate-duration', `${duration}ms`);
        el.style.setProperty('--animate-delay', `${delay}ms`);
        const cls = ['animate__animated', `animate__${name}`];
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          el.removeEventListener('animationend', onEnd);
          if (!persist) stripAnimate(el);
          resolve();
        };
        const onEnd = (e) => {
          if (e.target !== el) return;
          finish();
        };
        const timer = setTimeout(finish, duration + delay + 120);
        el.addEventListener('animationend', onEnd);
        el.classList.add(...cls);
      });
    },

    fadeIn(el, duration = 360) {
      return this.run(el, 'fadeIn', duration);
    },

    fadeOut(el, duration = 240) {
      return this.run(el, 'fadeOut', duration);
    },

    reveal(el, name = 'zoomIn', duration = 650, prefer = true) {
      if (!el || !this.shouldAnimate(prefer)) return;
      this.run(el, name, duration);
    },

    revealChildren(root, selector = '.media-card, .work-card, .empty-state, .empty-state.inline, .stat-pill', opts = {}) {
      const { prefer = true } = opts;
      if (!root || !this.shouldAnimate(prefer)) return;
      const {
        animation = 'zoomIn',
        duration = 650,
        stagger = 28,
      } = opts;
      const nodes = root.matches?.(selector) ? [root] : [...root.querySelectorAll(selector)];
      nodes.forEach((el, i) => this.run(el, animation, duration, i * stagger));
    },

    hideChildren(root, selector = '.media-card, .work-card, .empty-state, .empty-state.inline', opts = {}) {
      if (!root || !this.shouldAnimate(true)) return Promise.resolve();
      const { duration = 420, stagger = 0 } = opts;
      const nodes = root.matches?.(selector) ? [root] : [...root.querySelectorAll(selector)];
      if (!nodes.length) return Promise.resolve();
      return Promise.all(nodes.map((el, i) => this.run(el, 'zoomOut', duration, i * stagger)));
    },

    async swapChildren(root, html, prefer = true) {
      if (!root) return;
      const gen = (root._fxGen || 0) + 1;
      root._fxGen = gen;
      if (this.shouldAnimate(prefer) && root.children.length) {
        await this.hideChildren(root);
        if (root._fxGen !== gen) return;
      }
      root.innerHTML = html;
      if (root._fxGen !== gen) return;
      if (this.shouldAnimate(prefer)) this.revealChildren(root);
    },

    toastIn(el) {
      return this.run(el, 'zoomIn', 500);
    },

    toastOut(el) {
      return this.run(el, 'zoomOut', 380);
    },

    async openOverlay(overlay, inner) {
      if (!overlay) return;
      overlay.classList.add('open');
      stripAnimate(inner);
      if (!this.shouldAnimate(true)) return;
      await nextFrame();
      if (inner) await this.run(inner, 'zoomIn', 700);
    },

    async closeOverlay(overlay, inner) {
      if (!overlay) return;
      if (inner && this.shouldAnimate(true)) {
        await this.run(inner, 'zoomOut', 480, 0, { persist: true });
      }
      overlay.classList.remove('open');
      stripAnimate(inner);
    },
  };

  global.FX = FX;
})(window);
