import React, { useEffect, useRef, useState } from 'react';
import {
  Play,
  Pause,
  Rewind,
  FastForward,
  Volume2,
  VolumeX,
  RotateCcw,
  AlertTriangle,
} from 'lucide-react';

/**
 * AudioPlayer — real <audio> element backed by GET /calls/{id}/audio.
 *
 * Props:
 *   callId         (number|null)  — DB id; src = /calls/{callId}/audio
 *   currentTimeMs  (number)       — controlled playhead (ms)
 *   onTimeUpdate   (ms => void)   — parent syncs state on timeupdate
 *   seekToMs       (number|null)  — parent pushes a seek-to request (ms)
 *   onSeekConsumed (() => void)   — parent clears seekToMs after seek
 *   flaggedMoments (array)        — [{id, timestamp_ms, formatted_time, title}]
 *   onJumpToFlag   (flag => void) — cross-panel navigation
 */
export default function AudioPlayer({
  callId,
  currentTimeMs = 0,
  onTimeUpdate,
  seekToMs,
  onSeekConsumed,
  flaggedMoments = [],
  onJumpToFlag,
}) {
  const audioRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0); // seconds
  const [volume, setVolume] = useState(0.8);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [audioError, setAudioError] = useState(null);

  const src = callId ? `/calls/${callId}/audio` : null;

  // ── Seek from parent (evidence / flag clicks) ────────────────────────────
  useEffect(() => {
    if (seekToMs !== null && seekToMs !== undefined && audioRef.current) {
      audioRef.current.currentTime = seekToMs / 1000;
      // Auto-play after seek so user immediately hears the context
      audioRef.current.play().then(() => setIsPlaying(true)).catch(() => {});
      if (onSeekConsumed) onSeekConsumed();
    }
  }, [seekToMs, onSeekConsumed]);

  // ── Sync playback speed ──────────────────────────────────────────────────
  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = playbackSpeed;
  }, [playbackSpeed]);

  // ── Sync volume / mute ───────────────────────────────────────────────────
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = volume;
      audioRef.current.muted = isMuted;
    }
  }, [volume, isMuted]);

  // ── Space-bar shortcut ───────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      if (
        e.code === 'Space' &&
        e.target.tagName !== 'INPUT' &&
        e.target.tagName !== 'TEXTAREA' &&
        e.target.tagName !== 'SELECT'
      ) {
        e.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ── Audio element event handlers ─────────────────────────────────────────
  const handleLoadedMetadata = () => {
    if (audioRef.current) {
      setDuration(audioRef.current.duration || 0);
      setAudioError(null);
    }
  };

  const handleTimeUpdate = () => {
    if (audioRef.current && onTimeUpdate) {
      onTimeUpdate(audioRef.current.currentTime * 1000);
    }
  };

  const handleEnded = () => {
    setIsPlaying(false);
    if (onTimeUpdate) onTimeUpdate(0);
  };

  const handleError = () => {
    setAudioError('Audio file unavailable on server');
    setIsPlaying(false);
  };

  // ── Controls ─────────────────────────────────────────────────────────────
  const togglePlay = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
      setIsPlaying(false);
    } else {
      audioRef.current.play().catch(() => setAudioError('Browser blocked autoplay'));
      setIsPlaying(true);
    }
  };

  const handleSeek = (ms) => {
    const clamped = Math.max(0, Math.min(duration * 1000, ms));
    if (audioRef.current) audioRef.current.currentTime = clamped / 1000;
    if (onTimeUpdate) onTimeUpdate(clamped);
  };

  const formatTime = (sec) => {
    const s = Math.floor(sec || 0);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m < 10 ? '0' : ''}${m}:${r < 10 ? '0' : ''}${r}`;
  };

  const currentSec = currentTimeMs / 1000;
  const progressFraction = duration > 0 ? Math.min(1, currentSec / duration) : 0;

  return (
    <div className="waveform-console">
      {/* Hidden native audio element */}
      {src && (
        <audio
          ref={audioRef}
          src={src}
          onLoadedMetadata={handleLoadedMetadata}
          onTimeUpdate={handleTimeUpdate}
          onEnded={handleEnded}
          onError={handleError}
          preload="metadata"
        />
      )}

      {/* ── Progress / Seek Track ───────────────────────────────────────── */}
      <div
        className="waveform-canvas-row"
        style={{
          height: '40px',
          alignItems: 'center',
          cursor: src ? 'pointer' : 'default',
          position: 'relative',
        }}
        onClick={(e) => {
          if (!src) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
          handleSeek(ratio * duration * 1000);
        }}
        title={src ? 'Click to seek' : 'No audio loaded'}
      >
        {/* Track */}
        <div style={{
          position: 'absolute', left: 0, top: '50%', transform: 'translateY(-50%)',
          width: '100%', height: '4px',
          background: 'var(--border-medium)', borderRadius: '2px',
        }} />
        {/* Played fill */}
        <div style={{
          position: 'absolute', left: 0, top: '50%', transform: 'translateY(-50%)',
          width: `${progressFraction * 100}%`, height: '4px',
          background: 'var(--primary)', borderRadius: '2px',
          transition: 'width 0.08s linear',
        }} />
        {/* Scrub handle */}
        {src && duration > 0 && (
          <div style={{
            position: 'absolute',
            left: `calc(${progressFraction * 100}% - 6px)`,
            top: '50%', transform: 'translateY(-50%)',
            width: '13px', height: '13px', borderRadius: '50%',
            background: 'var(--primary)',
            boxShadow: '0 0 0 2px var(--bg-surface)',
            pointerEvents: 'none',
          }} />
        )}
        {/* Flag markers (positioned at actual timestamp) */}
        {duration > 0 && flaggedMoments.map((f) => (
          <div
            key={f.id || f.timestamp_ms}
            title={`${f.title || 'Flag'} — ${f.formatted_time || formatTime(f.timestamp_ms / 1000)}`}
            style={{
              position: 'absolute',
              left: `${((f.timestamp_ms / 1000) / duration) * 100}%`,
              top: '50%', transform: 'translate(-50%, -50%)',
              width: '9px', height: '9px', borderRadius: '50%',
              background: '#F59E0B', boxShadow: '0 0 6px #F59E0B',
              pointerEvents: 'none',
            }}
          />
        ))}
      </div>

      {/* ── Controls Row ───────────────────────────────────────────────────── */}
      <div className="waveform-controls-row">
        <div className="waveform-left-controls">
          {/* Play/Pause */}
          <button
            className="play-toggle-btn"
            onClick={togglePlay}
            title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
            disabled={!src}
          >
            {isPlaying ? <Pause size={17} /> : <Play size={17} style={{ marginLeft: '2px' }} />}
          </button>

          {/* −5s */}
          <button className="btn btn-ghost btn-sm" disabled={!src}
            onClick={() => handleSeek(Math.max(0, currentTimeMs - 5000))} title="Rewind 5s">
            <Rewind size={14} /> -5s
          </button>

          {/* +5s */}
          <button className="btn btn-ghost btn-sm" disabled={!src}
            onClick={() => handleSeek(Math.min(duration * 1000, currentTimeMs + 5000))} title="Forward 5s">
            +5s <FastForward size={14} />
          </button>

          {/* Restart */}
          <button className="btn btn-ghost btn-sm" disabled={!src}
            onClick={() => handleSeek(0)} title="Restart">
            <RotateCcw size={13} />
          </button>

          {/* Time readout */}
          <div className="time-readout">
            <span>{formatTime(currentSec)}</span>
            <span className="time-readout-total"> / {formatTime(duration)}</span>
          </div>

          {/* Volume */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginLeft: '10px' }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setIsMuted((m) => !m)}
              style={{ padding: '3px 6px' }}
              title={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted
                ? <VolumeX size={15} style={{ color: 'var(--text-muted)' }} />
                : <Volume2 size={15} style={{ color: 'var(--text-primary)' }} />}
            </button>
            <input
              type="range" min="0" max="1" step="0.05"
              value={isMuted ? 0 : volume}
              onChange={(e) => { setVolume(parseFloat(e.target.value)); setIsMuted(false); }}
              style={{ width: '60px', accentColor: 'var(--primary)', height: '4px', cursor: 'pointer' }}
            />
          </div>
        </div>

        {/* Right: error / no-audio notice, flag jump, speed */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {!src && (
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Upload a call to enable audio playback
            </span>
          )}
          {audioError && (
            <span style={{ fontSize: '11px', color: 'var(--warning-light)' }}>
              {audioError}
            </span>
          )}

          {flaggedMoments.length > 0 && src && (
            <button
              className="btn btn-secondary btn-sm"
              style={{ borderColor: 'rgba(245,158,11,0.3)', color: '#FBBF24' }}
              onClick={() => onJumpToFlag && onJumpToFlag(flaggedMoments[0])}
              title="Jump to first flagged moment"
            >
              <AlertTriangle size={13} />
              <span>
                Jump to Flag ({flaggedMoments[0].formatted_time || formatTime(flaggedMoments[0].timestamp_ms / 1000)})
              </span>
            </button>
          )}

          <select
            value={playbackSpeed}
            onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
            style={{
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-mono)',
              fontSize: '12px',
              padding: '4px 8px',
              borderRadius: 'var(--radius-sm)',
              outline: 'none',
              cursor: 'pointer',
            }}
          >
            <option value={0.75}>0.75x</option>
            <option value={1}>1.0x</option>
            <option value={1.25}>1.25x</option>
            <option value={1.5}>1.5x</option>
            <option value={2}>2.0x</option>
          </select>
        </div>
      </div>
    </div>
  );
}

