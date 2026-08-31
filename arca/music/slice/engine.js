(() => {
  'use strict';

  const SEED = 184729;
  const BPM = 52;
  const BEATS_PER_BAR = 4;
  const BEAT_SECONDS = 60 / BPM;
  const BAR_SECONDS = BEAT_SECONDS * BEATS_PER_BAR;
  const TOTAL_BARS = 65;
  const TICK_MS = 120;
  const LOOKAHEAD = 2.6;
  const DRONE_EVERY = 3;
  const PAD_OVERLAP = 1.2;
  const ORIGINS = {
    rendered: { base: 'synth', label: 'rendered' },
    sampled: { base: 'sampled', label: 'sampled' },
  };

  const PEDAL = 45;

  const PROGRESSION = [
    { bass: 45, tones: [57, 60, 64, 67], colour: [69, 72, 76] },
    { bass: 41, tones: [60, 65, 69, 72], colour: [72, 76, 77] },
    { bass: 48, tones: [59, 64, 67, 72], colour: [71, 76, 79] },
    { bass: 52, tones: [59, 62, 64, 67], colour: [71, 74, 76] },
    { bass: 50, tones: [57, 60, 62, 65], colour: [69, 72, 74] },
    { bass: 41, tones: [60, 65, 69, 76], colour: [72, 76, 77] },
    { bass: 43, tones: [57, 62, 67, 71], colour: [69, 74, 79] },
    { bass: 45, tones: [59, 60, 64, 69], colour: [71, 72, 76] },
  ];

  const BUS_SHAPE = {
    low: { cutoff: 1400, send: 0.34, gain: 0.95 },
    mid: { cutoff: 5200, send: 0.5, gain: 0.66 },
    high: { cutoff: 12000, send: 0.62, gain: 0.42 },
  };

  const SHAPE = {
    drone: { attack: 2.2, release: 3.2, bus: 'low', cutoff: 700 },
    pad: { attack: 1.4, release: 2.6, bus: 'low', cutoff: 2600 },
    keys: { attack: 0.006, release: 1.2, bus: 'mid', cutoff: 6000 },
    bell: { attack: 0.002, release: 1.8, bus: 'high', cutoff: 11000 },
  };

  function mulberry32(seed) {
    let state = seed >>> 0;
    return () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let value = Math.imul(state ^ (state >>> 15), 1 | state);
      value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function scriptedIntensity(bar) {
    if (bar < 22) return 0.2;
    if (bar < 44) return 0.55;
    return 0.9;
  }

  function pickSample(bank, note) {
    let best = bank[0];
    for (const candidate of bank) {
      if (Math.abs(candidate.note - note) < Math.abs(best.note - note)) best = candidate;
    }
    return best;
  }

  function makeBus(context, shape, master, convolver) {
    const input = context.createGain();
    const tone = context.createBiquadFilter();
    const dry = context.createGain();
    const send = context.createGain();
    tone.type = 'lowpass';
    tone.frequency.value = shape.cutoff;
    tone.Q.value = 0.5;
    dry.gain.value = shape.gain;
    send.gain.value = shape.send * shape.gain;
    input.connect(tone);
    tone.connect(dry);
    dry.connect(master);
    tone.connect(send);
    send.connect(convolver);
    return { input, tone, dry, send };
  }

  const state = {
    origin: 'rendered',
    material: new Map(),
    engine: null,
    auto: true,
    manual: 0.2,
    volume: 0.7,
    voices: 0,
    peakVoices: 0,
    bar: 0,
    loading: false,
  };

  const element = {
    rendered: document.getElementById('pick-rendered'),
    sampled: document.getElementById('pick-sampled'),
    play: document.getElementById('play'),
    stop: document.getElementById('stop'),
    volume: document.getElementById('volume'),
    auto: document.getElementById('auto'),
    intensity: document.getElementById('intensity'),
    render: document.getElementById('render'),
    status: document.getElementById('status'),
  };

  let statusLine = 'idle';

  function report(line) {
    statusLine = line;
    draw();
  }

  function draw() {
    const engine = state.engine;
    if (!engine) {
      element.status.textContent = statusLine;
      return;
    }
    const elapsed = Math.max(engine.context.currentTime - engine.origin, 0);
    const minutes = Math.floor(elapsed / 60);
    const seconds = Math.floor(elapsed % 60).toString().padStart(2, '0');
    const intensity = Math.round(currentIntensity(state.bar) * 100);
    element.status.innerHTML =
      `<b>${ORIGINS[state.origin].label}</b> · october · ambient · seed ${SEED} · gen v0\n` +
      `${minutes}:${seconds} · bar ${Math.min(state.bar, TOTAL_BARS)}/${TOTAL_BARS} · ` +
      `intensity ${intensity}% · voices ${state.voices} (peak ${state.peakVoices})`;
  }

  function currentIntensity(bar) {
    return state.auto ? scriptedIntensity(bar) : state.manual;
  }

  async function loadMaterial(context, origin) {
    const key = `${origin}|${context.constructor.name}`;
    if (state.material.has(key)) return state.material.get(key);
    const base = ORIGINS[origin].base;
    const response = await fetch(`${base}/manifest.json`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`no manifest for ${origin}`);
    const manifest = await response.json();
    const banks = new Map();
    const decode = async (file) => {
      const raw = await fetch(`${base}/${file}`, { cache: 'no-store' });
      if (!raw.ok) throw new Error(`missing ${file}`);
      return context.decodeAudioData(await raw.arrayBuffer());
    };
    for (const entry of manifest.entries) {
      const buffer = await decode(entry.file);
      if (!banks.has(entry.instrument)) banks.set(entry.instrument, []);
      banks.get(entry.instrument).push({ note: entry.note, buffer });
    }
    for (const bank of banks.values()) bank.sort((a, b) => a.note - b.note);
    const impulse = await decode(manifest.impulses[0].file);
    const loaded = { banks, impulse, manifest };
    state.material.set(key, loaded);
    return loaded;
  }

  function buildEngine(context, material) {
    const master = context.createGain();
    const limiter = context.createDynamicsCompressor();
    const convolver = context.createConvolver();
    const wet = context.createGain();
    master.gain.value = state.volume;
    limiter.threshold.value = -6;
    limiter.knee.value = 0;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.003;
    limiter.release.value = 0.25;
    convolver.buffer = material.impulse;
    convolver.normalize = true;
    wet.gain.value = 0.9;
    convolver.connect(wet);
    wet.connect(master);
    master.connect(limiter);
    limiter.connect(context.destination);
    const buses = {};
    for (const [name, shape] of Object.entries(BUS_SHAPE)) {
      buses[name] = makeBus(context, shape, master, convolver);
    }
    return {
      context,
      live: typeof OfflineAudioContext === 'undefined' || !(context instanceof OfflineAudioContext),
      material,
      master,
      limiter,
      convolver,
      buses,
      random: mulberry32(SEED),
      origin: context.currentTime + 0.35,
      nextBar: 0,
      timer: 0,
    };
  }

  function voice(engine, instrument, note, when, hold, level) {
    const bank = engine.material.banks.get(instrument);
    if (!bank || bank.length === 0) return;
    const shape = SHAPE[instrument];
    const sample = pickSample(bank, note);
    const context = engine.context;
    const source = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const gain = context.createGain();
    const cents = (engine.random() - 0.5) * 16;
    source.buffer = sample.buffer;
    source.playbackRate.value = Math.pow(2, (note - sample.note) / 12 + cents / 1200);
    filter.type = 'lowpass';
    filter.frequency.value = shape.cutoff;
    filter.Q.value = 0.4;
    const peak = Math.max(level, 0.0004);
    const attack = Math.min(shape.attack, Math.max(hold * 0.5, 0.004));
    const sustainEnd = when + Math.max(hold, attack + 0.05);
    gain.gain.setValueAtTime(0.0004, when);
    gain.gain.exponentialRampToValueAtTime(peak, when + attack);
    gain.gain.setValueAtTime(peak, sustainEnd);
    gain.gain.exponentialRampToValueAtTime(0.0004, sustainEnd + shape.release);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(engine.buses[shape.bus].input);
    source.start(when);
    source.stop(sustainEnd + shape.release + 0.05);
    if (!engine.live) return;
    state.voices += 1;
    state.peakVoices = Math.max(state.peakVoices, state.voices);
    source.onended = () => {
      source.disconnect();
      filter.disconnect();
      gain.disconnect();
      state.voices = Math.max(state.voices - 1, 0);
    };
  }

  function scheduleBar(engine, bar) {
    const at = engine.origin + bar * BAR_SECONDS;
    const chord = PROGRESSION[Math.floor(bar / 2) % PROGRESSION.length];
    const intensity = currentIntensity(bar);
    const random = engine.random;

    if (bar % DRONE_EVERY === 0) {
      voice(engine, 'drone', PEDAL, at, BAR_SECONDS * DRONE_EVERY, 0.32 + 0.1 * intensity);
    }

    if (bar % 2 === 0) {
      const width = intensity < 0.35 ? 2 : intensity < 0.7 ? 3 : chord.tones.length;
      for (let index = 0; index < width; index += 1) {
        const spread = random() * 0.5;
        const level = (0.15 + 0.07 * intensity) / (1 + 0.25 * index);
        const when = Math.max(at - PAD_OVERLAP + spread, engine.origin);
        voice(engine, 'pad', chord.tones[index], when, BAR_SECONDS * 2, level);
      }
    }

    if (intensity >= 0.3) {
      const density = 0.3 + 0.5 * intensity;
      for (let beat = 0; beat < BEATS_PER_BAR; beat += 1) {
        if (random() > density) continue;
        const pool = random() < 0.25 ? chord.colour : chord.tones;
        const note = pool[Math.floor(random() * pool.length)];
        const when = at + beat * BEAT_SECONDS + (random() - 0.5) * 0.03;
        voice(engine, 'keys', note, when, BEAT_SECONDS * 1.5, 0.11 + 0.09 * intensity);
      }
      if (bar % 2 === 0 && random() < 0.35 + 0.4 * intensity) {
        voice(engine, 'keys', chord.bass, at + 0.02, BEAT_SECONDS * 3, 0.13);
      }
    }

    if (intensity >= 0.12 && random() < 0.18 + 0.42 * intensity) {
      const pool = intensity >= 0.7 ? chord.colour : chord.tones;
      const note = pool[Math.floor(random() * pool.length)];
      const when = at + Math.floor(random() * BEATS_PER_BAR) * BEAT_SECONDS;
      voice(engine, 'bell', note, when, BEAT_SECONDS * 3, 0.08 + 0.06 * intensity);
    }
  }

  function tick() {
    const engine = state.engine;
    if (!engine) return;
    const horizon = engine.context.currentTime + LOOKAHEAD;
    while (engine.nextBar < TOTAL_BARS && engine.origin + engine.nextBar * BAR_SECONDS < horizon) {
      scheduleBar(engine, engine.nextBar);
      engine.nextBar += 1;
    }
    state.bar = Math.max(
      0,
      Math.floor((engine.context.currentTime - engine.origin) / BAR_SECONDS)
    );
    if (engine.nextBar >= TOTAL_BARS && state.bar >= TOTAL_BARS) {
      report('finished — five minutes played to the end');
      stop(true);
      return;
    }
    draw();
  }

  function stop(finished) {
    const engine = state.engine;
    if (!engine) return;
    window.clearInterval(engine.timer);
    const now = engine.context.currentTime;
    engine.master.gain.cancelScheduledValues(now);
    engine.master.gain.setValueAtTime(engine.master.gain.value, now);
    engine.master.gain.linearRampToValueAtTime(0.0001, now + (finished ? 2.5 : 0.6));
    const context = engine.context;
    state.engine = null;
    state.bar = 0;
    window.setTimeout(() => {
      context.close().catch(() => {});
      state.voices = 0;
    }, (finished ? 3.2 : 1.2) * 1000);
    element.play.disabled = false;
    element.stop.disabled = true;
    if (!finished) report('stopped');
  }

  async function play() {
    if (state.engine || state.loading) return;
    state.loading = true;
    element.play.disabled = true;
    report(`loading ${ORIGINS[state.origin].label} material…`);
    let context = null;
    try {
      context = new (window.AudioContext || window.webkitAudioContext)();
      await context.resume();
      const material = await loadMaterial(context, state.origin);
      state.peakVoices = 0;
      state.voices = 0;
      const engine = buildEngine(context, material);
      state.engine = engine;
      engine.timer = window.setInterval(tick, TICK_MS);
      element.stop.disabled = false;
      tick();
    } catch (error) {
      if (context) context.close().catch(() => {});
      state.engine = null;
      element.play.disabled = false;
      report(`could not start: ${error && error.message ? error.message : 'unknown reason'}`);
    } finally {
      state.loading = false;
    }
  }

  async function renderOffline(origin) {
    const tail = 6;
    const seconds = TOTAL_BARS * BAR_SECONDS + tail;
    const context = new OfflineAudioContext(1, Math.ceil(seconds * 44100), 44100);
    const material = await loadMaterial(context, origin);
    const wasAuto = state.auto;
    const wasVolume = state.volume;
    state.auto = true;
    state.volume = 0.85;
    try {
      const engine = buildEngine(context, material);
      engine.origin = 0.5;
      for (let bar = 0; bar < TOTAL_BARS; bar += 1) scheduleBar(engine, bar);
      return await context.startRendering();
    } finally {
      state.auto = wasAuto;
      state.volume = wasVolume;
    }
  }

  function bufferToWav(buffer) {
    const samples = buffer.getChannelData(0);
    const bytes = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(bytes);
    const text = (offset, value) => {
      for (let index = 0; index < value.length; index += 1) {
        view.setUint8(offset + index, value.charCodeAt(index));
      }
    };
    text(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    text(8, 'WAVEfmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, buffer.sampleRate, true);
    view.setUint32(28, buffer.sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    text(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    for (let index = 0; index < samples.length; index += 1) {
      const clamped = Math.max(-1, Math.min(1, samples[index]));
      view.setInt16(44 + index * 2, Math.round(clamped * 32767), true);
    }
    return new Blob([bytes], { type: 'audio/wav' });
  }

  async function exportWav() {
    if (state.engine || state.loading) return;
    state.loading = true;
    element.render.disabled = true;
    const origin = state.origin;
    report(`rendering five minutes of ${ORIGINS[origin].label} material offline…`);
    try {
      const buffer = await renderOffline(origin);
      const url = URL.createObjectURL(bufferToWav(buffer));
      const link = document.createElement('a');
      link.href = url;
      link.download = `arca-music-f9.0-${origin}.wav`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      report(`rendered arca-music-f9.0-${origin}.wav`);
    } catch (error) {
      report(`could not render: ${error && error.message ? error.message : 'unknown reason'}`);
    } finally {
      element.render.disabled = false;
      state.loading = false;
    }
  }

  function selectOrigin(origin) {
    if (state.engine) stop(false);
    state.origin = origin;
    element.rendered.setAttribute('aria-pressed', String(origin === 'rendered'));
    element.sampled.setAttribute('aria-pressed', String(origin === 'sampled'));
    report(`material set to ${ORIGINS[origin].label}`);
  }

  element.rendered.addEventListener('click', () => selectOrigin('rendered'));
  element.sampled.addEventListener('click', () => selectOrigin('sampled'));
  element.play.addEventListener('click', play);
  element.stop.addEventListener('click', () => stop(false));
  element.render.addEventListener('click', exportWav);

  element.volume.addEventListener('input', () => {
    state.volume = Number(element.volume.value) / 100;
    const engine = state.engine;
    if (!engine) return;
    const now = engine.context.currentTime;
    engine.master.gain.cancelScheduledValues(now);
    engine.master.gain.setValueAtTime(engine.master.gain.value, now);
    engine.master.gain.linearRampToValueAtTime(Math.max(state.volume, 0.0001), now + 0.08);
  });

  element.auto.addEventListener('click', () => {
    state.auto = !state.auto;
    element.auto.setAttribute('aria-pressed', String(state.auto));
    element.auto.textContent = state.auto ? 'Intensity: scripted' : 'Intensity: manual';
    element.intensity.disabled = state.auto;
    draw();
  });

  element.intensity.addEventListener('input', () => {
    state.manual = Number(element.intensity.value) / 100;
    draw();
  });

  window.arcaSlice = { renderOffline, TOTAL_BARS, BAR_SECONDS };

  report('idle — pick a material and press play');
})();
