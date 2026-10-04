/**
 * Web Audio Speech Synthesis Simulator
 * Generates natural, gentle conversational acoustic formants when playing,
 * providing real audio feedback to user clicks with volume and mute controls.
 */

class AudioSynthEngine {
  constructor() {
    this.ctx = null;
    this.osc1 = null;
    this.osc2 = null;
    this.gainNode = null;
    this.filterNode = null;
    this.isPlaying = false;
    this.isMuted = false;
    this.volume = 0.3;
  }

  init() {
    if (!this.ctx) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (AudioContext) {
        this.ctx = new AudioContext();
      }
    }
  }

  start() {
    this.init();
    if (!this.ctx || this.isPlaying) return;

    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }

    try {
      // Main fundamental frequency oscillator
      this.osc1 = this.ctx.createOscillator();
      this.osc2 = this.ctx.createOscillator();

      // Bandpass formant filter for speech resonance
      this.filterNode = this.ctx.createBiquadFilter();
      this.filterNode.type = 'bandpass';
      this.filterNode.frequency.value = 680; // typical vocal vowel formant
      this.filterNode.Q.value = 3.5;

      // Master gain
      this.gainNode = this.ctx.createGain();
      this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : this.volume * 0.12, this.ctx.currentTime);

      this.osc1.type = 'triangle';
      this.osc1.frequency.setValueAtTime(185, this.ctx.currentTime); // male/female conversational pitch

      this.osc2.type = 'sine';
      this.osc2.frequency.setValueAtTime(370, this.ctx.currentTime); // harmonic

      // Natural speech-like subtle cadence modulation
      const now = this.ctx.currentTime;
      this.osc1.frequency.setTargetAtTime(210, now + 0.3, 0.4);
      this.osc1.frequency.setTargetAtTime(175, now + 0.9, 0.5);

      this.osc1.connect(this.filterNode);
      this.osc2.connect(this.filterNode);
      this.filterNode.connect(this.gainNode);
      this.gainNode.connect(this.ctx.destination);

      this.osc1.start();
      this.osc2.start();
      this.isPlaying = true;
    } catch {
      // Graceful fallback if audio context blocked by browser autoplay policy
    }
  }

  stop() {
    if (!this.isPlaying) return;
    try {
      if (this.osc1) {
        this.osc1.stop();
        this.osc1.disconnect();
      }
      if (this.osc2) {
        this.osc2.stop();
        this.osc2.disconnect();
      }
    } catch {
      // Ignore stop errors
    }
    this.isPlaying = false;
  }

  setVolume(vol) {
    this.volume = vol;
    if (this.gainNode && this.ctx) {
      this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : this.volume * 0.12, this.ctx.currentTime);
    }
  }

  toggleMute() {
    this.isMuted = !this.isMuted;
    if (this.gainNode && this.ctx) {
      this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : this.volume * 0.12, this.ctx.currentTime);
    }
    return this.isMuted;
  }
}

export const audioSynth = new AudioSynthEngine();
