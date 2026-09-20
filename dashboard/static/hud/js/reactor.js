// hud/js/reactor.js — canvas renderer for the JARVIS AI CORE / reactor.
// Pure 2D canvas (no WebGL, no dependencies): concentric rings, radial ticks,
// crosshair, scanning radar, orbiting particles and a pulsing core.  Visual
// states mirror the real task/mic state the controller feeds in.

const STATES = {
  IDLE:       { ring: "#2fe3ff", inner: "#ff2f6d", speed: 0.10, glow: 0.32, pulse: 0.35, sweep: "#2fe3ff", sweepAlpha: 0.06 },
  LISTENING:  { ring: "#2fe3ff", inner: "#2fe3ff", speed: 0.22, glow: 0.55, pulse: 0.95, sweep: "#9df3ff", sweepAlpha: 0.12 },
  THINKING:   { ring: "#2fe3ff", inner: "#2f8dff", speed: 0.75, glow: 0.68, pulse: 0.75, sweep: "#9df3ff", sweepAlpha: 0.16 },
  EXECUTING:  { ring: "#2fe3ff", inner: "#ff2f6d", speed: 1.15, glow: 0.85, pulse: 0.60, sweep: "#2fe3ff", sweepAlpha: 0.22 },
  PERMISSION: { ring: "#ffb02e", inner: "#ff2f6d", speed: 0.45, glow: 0.80, pulse: 1.15, sweep: "#ffb02e", sweepAlpha: 0.18 },
  SUCCESS:    { ring: "#46ffa8", inner: "#46ffa8", speed: 0.30, glow: 0.70, pulse: 1.00, sweep: "#46ffa8", sweepAlpha: 0.14 },
  ERROR:      { ring: "#ff4a4a", inner: "#ff2f6d", speed: 0.55, glow: 0.82, pulse: 1.35, sweep: "#ff4a4a", sweepAlpha: 0.20 },
  OFFLINE:    { ring: "#3d5866", inner: "#3d5866", speed: 0.03, glow: 0.10, pulse: 0.15, sweep: "#3d5866", sweepAlpha: 0.03 },
};

const PARTICLE_COUNT = 26;
const TICKS = 72;

function alpha(hex, value) {
  const clean = hex.replace("#", "");
  const number = parseInt(clean, 16);
  const r = (number >> 16) & 255;
  const g = (number >> 8) & 255;
  const b = number & 255;
  return `rgba(${r}, ${g}, ${b}, ${value})`;
}

export function createReactor(canvas) {
  const ctx = canvas.getContext("2d");
  let width = 1;
  let height = 1;
  let stateName = "IDLE";
  let level = 0;
  let spin = 0;
  let sweepAngle = 0;
  let scan = 0;
  let last = 0;
  let raf = 0;
  let running = false;
  let particles = [];
  const motionMedia = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
  let reducedMotion = Boolean(motionMedia && motionMedia.matches);

  const palette = () => STATES[stateName] || STATES.IDLE;

  function seedParticles() {
    particles = Array.from({ length: PARTICLE_COUNT }, () => ({
      radius: 0.26 + Math.random() * 0.7,
      angle: Math.random() * Math.PI * 2,
      speed: (0.1 + Math.random() * 0.5) * (Math.random() < 0.5 ? -1 : 1),
      size: 0.5 + Math.random() * 1.7,
      alpha: 0.3 + Math.random() * 0.55,
    }));
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = Math.max(1, rect.width);
    height = Math.max(1, rect.height);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seedParticles();
    if (running && reducedMotion) paint(performance.now());
  }

  function ring(radius, color, lineWidth, dashed) {
    ctx.beginPath();
    ctx.setLineDash(dashed ? [radius * 0.045, radius * 0.035] : []);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.arc(0, 0, radius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawTicks(base, color) {
    for (let index = 0; index < TICKS; index += 1) {
      const angle = (index / TICKS) * Math.PI * 2 + spin;
      const major = index % 6 === 0;
      const inner = major ? base * 0.9 : base * 0.945;
      ctx.beginPath();
      ctx.strokeStyle = alpha(color, major ? 0.55 : 0.22);
      ctx.lineWidth = major ? 1.2 : 0.7;
      ctx.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
      ctx.lineTo(Math.cos(angle) * base, Math.sin(angle) * base);
      ctx.stroke();
    }
  }

  function drawCrosshair(radius, color) {
    ctx.strokeStyle = alpha(color, 0.16);
    ctx.lineWidth = 0.7;
    for (let index = 0; index < 4; index += 1) {
      const angle = (index / 4) * Math.PI * 2 + Math.PI / 4;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * radius * 0.12, Math.sin(angle) * radius * 0.12);
      ctx.lineTo(Math.cos(angle) * radius, Math.sin(angle) * radius);
      ctx.stroke();
    }
  }
function drawSweep(radius, color, strength) {
    if (!ctx.createConicGradient) return;
    const gradient = ctx.createConicGradient(sweepAngle, 0, 0);
    gradient.addColorStop(0, alpha(color, strength));
    gradient.addColorStop(0.12, alpha(color, strength * 0.35));
    gradient.addColorStop(0.3, alpha(color, 0));
    gradient.addColorStop(1, alpha(color, 0));
    ctx.beginPath();
    ctx.fillStyle = gradient;
    ctx.arc(0, 0, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawParticles(radius, color, dt) {
    const boost = 0.25 + palette().speed;
    for (const particle of particles) {
      particle.angle += particle.speed * dt * boost;
      const x = Math.cos(particle.angle) * particle.radius * radius;
      const y = Math.sin(particle.angle) * particle.radius * radius;
      ctx.beginPath();
      ctx.fillStyle = alpha(color, particle.alpha * (0.5 + palette().glow));
      ctx.arc(x, y, particle.size, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawCore(radius, palette_, strength) {
    const coreRadius = radius * (0.17 + strength * 0.05 + level * 0.06);
    const glow = ctx.createRadialGradient(0, 0, coreRadius * 0.1, 0, 0, coreRadius * 2.6);
    glow.addColorStop(0, alpha("#eafcff", 0.95));
    glow.addColorStop(0.22, alpha(palette_.ring, 0.8));
    glow.addColorStop(0.62, alpha(palette_.inner, 0.22));
    glow.addColorStop(1, alpha(palette_.inner, 0));
    ctx.beginPath();
    ctx.fillStyle = glow;
    ctx.arc(0, 0, coreRadius * 2.6, 0, Math.PI * 2);
    ctx.fill();

    ctx.beginPath();
    ctx.strokeStyle = alpha("#eafcff", 0.85);
    ctx.lineWidth = 1.1;
    const sides = 6;
    for (let index = 0; index <= sides; index += 1) {
      const angle = (index / sides) * Math.PI * 2 - spin * 1.4;
      const x = Math.cos(angle) * coreRadius;
      const y = Math.sin(angle) * coreRadius;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  function paint(now) {
    if (document.hidden) return;

    const dt = reducedMotion ? 0 : Math.min(0.05, (now - last) / 1000 || 0.016);
    last = now;

    const palette_ = palette();
    const radius = Math.min(width, height) / 2 - 4;
    if (radius <= 8) return;

    spin += dt * palette_.speed * 1.2;
    sweepAngle += dt * (0.35 + palette_.speed * 1.6);
    scan = (scan + dt * (0.2 + palette_.speed * 0.5)) % 1;
    const pulse = 0.5 + 0.5 * Math.sin(spin * 3.1);

    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(width / 2, height / 2);

    // faint guide rings + crosshair
    ctx.beginPath();
    ctx.strokeStyle = alpha(palette_.ring, 0.05);
    ctx.lineWidth = 0.6;
    for (let ringIndex = 1; ringIndex <= 5; ringIndex += 1) {
      ctx.beginPath();
      ctx.arc(0, 0, (radius * ringIndex) / 5, 0, Math.PI * 2);
      ctx.stroke();
    }
    drawCrosshair(radius, palette_.ring);

    drawSweep(radius, palette_.sweep, palette_.sweepAlpha);
    drawTicks(radius, palette_.ring);

    ring(radius, alpha(palette_.ring, 0.75), 1.2, true);
    ring(radius * 0.86, alpha(palette_.ring, 0.35), 0.8, false);
    ring(radius * 0.7, alpha(palette_.ring, 0.55), 1.1, true);
    ring(radius * 0.5, alpha(palette_.inner, 0.5), 0.9, false);
    ring(radius * 0.34, alpha(palette_.inner, 0.7), 1.3, true);

    // expanding scan ring
    const scanRadius = radius * (0.2 + scan * 0.8);
    ctx.beginPath();
    ctx.strokeStyle = alpha(palette_.sweep, (1 - scan) * 0.45 * palette_.pulse);
    ctx.lineWidth = 1.4;
    ctx.arc(0, 0, scanRadius, 0, Math.PI * 2);
    ctx.stroke();

    // orbital elements
    ctx.save();
    ctx.rotate(spin * 2.2);
    ctx.beginPath();
    ctx.strokeStyle = alpha(palette_.ring, 0.6);
    ctx.lineWidth = 2;
    ctx.arc(0, 0, radius * 0.94, 0, 0.5);
    ctx.stroke();
    ctx.beginPath();
    ctx.strokeStyle = alpha(palette_.inner, 0.7);
    ctx.lineWidth = 2;
    ctx.arc(0, 0, radius * 0.44, Math.PI * 0.9, Math.PI * 1.35);
    ctx.stroke();
    ctx.restore();

    drawParticles(radius, palette_.inner, dt);
    drawCore(radius, palette_, pulse * palette_.pulse);
    ctx.restore();
  }

  function schedule() {
    if (running && !reducedMotion && !document.hidden && !raf) {
      raf = requestAnimationFrame(frame);
    }
  }

  function frame(now) {
    raf = 0;
    paint(now);
    schedule();
  }

  function refreshMotion() {
    if (!running) return;
    cancelAnimationFrame(raf);
    raf = 0;
    last = performance.now();
    paint(last);
    schedule();
  }

  function onMotionChange(event) {
    reducedMotion = event.matches;
    refreshMotion();
  }

  function onVisibilityChange() {
    if (!document.hidden) refreshMotion();
  }

  return {
    start() {
      if (running) return;
      running = true;
      last = performance.now();
      resize();
      if (motionMedia) {
        if (motionMedia.addEventListener) motionMedia.addEventListener("change", onMotionChange);
        else motionMedia.addListener(onMotionChange);
      }
      document.addEventListener("visibilitychange", onVisibilityChange);
      paint(last);
      schedule();
    },
    stop() {
      running = false;
      cancelAnimationFrame(raf);
      raf = 0;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (motionMedia) {
        if (motionMedia.removeEventListener) motionMedia.removeEventListener("change", onMotionChange);
        else motionMedia.removeListener(onMotionChange);
      }
    },
    resize,
    setState(next) {
      if (STATES[next]) stateName = next;
      if (reducedMotion) paint(performance.now());
    },
    setLevel(next) {
      level = Math.max(0, Math.min(1, Number(next) || 0));
      if (reducedMotion) paint(performance.now());
    },
  };
}
