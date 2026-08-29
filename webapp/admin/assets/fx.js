/**
 * UI motion — wraps local animate.css (MIT, animate.style v4.1.1).
 * No external CDN; all assets under /admin/assets/vendor/.
 */
(function (global) {
  const reduced = () => global.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const FX = {
    silent: false,
    _silentDepth: 0,

    setSilent(on) {
      if (on) this._silentDepth += 1;
      else this._silentDepth = Math.max(0, this._silentDepth - 1);
      this.silent = this._silentDepth > 0;
    },

    shouldAnimate(prefer = true) {
      return !!prefer && !this.silent && !reduced();
    },

    run(el, name, duration = 400, delay = 0) {
      if (!el || !this.shouldAnimate(true)) return Promise.resolve();
      return new Promise((resolve) => {
        el.style.setProperty('--animate-duration', `${duration}ms`);
        el.style.setProperty('--animate-delay', `${delay}ms`);
        const cls = ['animate__animated', `animate__${name}`];
        const done = (e) => {
          if (e.target !== el) return;
          el.classList.remove(...cls);
          el.removeEventListener('animationend', done);
          resolve();
        };
        el.addEventListener('animationend', done);
        el.classList.add(...cls);
      });
    },

    fadeIn(el, duration = 360) {
      return this.run(el, 'fadeIn', duration);
    },

    fadeOut(el, duration = 240) {
      return this.run(el, 'fadeOut', duration);
    },

    reveal(el, name = 'fadeIn', duration = 380, prefer = true) {
      if (!el || !this.shouldAnimate(prefer)) return;
      this.run(el, name, duration);
    },

    revealChildren(root, selector = '.media-card, .work-card, .empty-state, .empty-state.inline, .stat-pill', opts = {}) {
      const { prefer = true } = opts;
      if (!root || !this.shouldAnimate(prefer)) return;
      const {
        animation = 'fadeInUp',
        duration = 420,
        stagger = 48,
      } = opts;
      const nodes = root.matches?.(selector) ? [root] : [...root.querySelectorAll(selector)];
      nodes.forEach((el, i) => this.run(el, animation, duration, i * stagger));
    },

    toastIn(el) {
      return this.run(el, 'fadeInUp', 320);
    },

    toastOut(el) {
      return this.run(el, 'fadeOut', 220);
    },

    async openOverlay(overlay, inner) {
      if (!overlay) return;
      overlay.classList.add('open');
      if (!this.silent) {
        await this.fadeIn(overlay, 280);
        if (inner) this.reveal(inner, 'fadeInUp', 360);
      }
    },

    async closeOverlay(overlay, inner) {
      if (!overlay) return;
      if (!this.silent && !reduced()) {
        if (inner) await this.fadeOut(inner, 180);
        await this.fadeOut(overlay, 220);
      }
      overlay.classList.remove('open');
    },
  };

  global.FX = FX;
})(window);
