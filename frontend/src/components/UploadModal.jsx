import React, { useState, useEffect } from 'react';
import {
  UploadCloud,
  FileAudio,
  CheckCircle2,
  RefreshCw,
  AlertCircle,
  User,
  Plus,
  Languages,
} from 'lucide-react';
import { apiFetch, apiJson } from '../api/client';

export default function UploadModal({ onCompleteUpload, onCancel }) {
  const [file, setFile] = useState(null);
  const [counsellors, setCounsellors] = useState([]);
  const [counsellorId, setCounsellorId] = useState('');
  const [newCounsellorName, setNewCounsellorName] = useState('');
  const [showAddCounsellor, setShowAddCounsellor] = useState(false);
  const [transcriptionMode, setTranscriptionMode] = useState('auto');
  const [isProcessing, setIsProcessing] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState(null);

  // Upload limits loaded dynamically from backend environment
  const [limits, setLimits] = useState({
    max_upload_mb: 200,
    max_upload_bytes: 200 * 1024 * 1024,
    max_audio_minutes: 120,
    allowed_audio_extensions: ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac'],
  });

  // Fetch upload limits and real counsellors on mount
  useEffect(() => {
    apiJson('/calls/upload-limits')
      .then((data) => {
        if (data && data.max_upload_mb) {
          setLimits(data);
        }
      })
      .catch(() => {
        // Fall back to safe defaults
      });

    apiJson('/counsellors')
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setCounsellors(data);
          setCounsellorId(data[0].id);
        }
      })
      .catch(() => {
        // Handled gracefully
      });
  }, []);

  const validateAndSetFile = (selectedFile) => {
    if (!selectedFile) return;

    // 1. Check extension
    const ext = '.' + selectedFile.name.split('.').pop().toLowerCase();
    if (!limits.allowed_audio_extensions.includes(ext)) {
      setErrorMessage(
        `File type '${ext}' is not supported. Allowed formats: ${limits.allowed_audio_extensions.join(', ')}`
      );
      setFile(null);
      return;
    }

    // 2. Check file size
    if (selectedFile.size > limits.max_upload_bytes) {
      setErrorMessage(
        `File size (${(selectedFile.size / (1024 * 1024)).toFixed(1)} MB) exceeds maximum allowed limit (${limits.max_upload_mb} MB).`
      );
      setFile(null);
      return;
    }

    // 3. Check audio duration (pre-check via HTML5 Audio element)
    try {
      const objectUrl = URL.createObjectURL(selectedFile);
      const audio = new Audio();
      audio.src = objectUrl;
      audio.onloadedmetadata = () => {
        URL.revokeObjectURL(objectUrl);
        const durationMin = audio.duration / 60;
        if (durationMin > limits.max_audio_minutes) {
          setErrorMessage(
            `Audio duration (${durationMin.toFixed(1)} min) exceeds maximum limit of ${limits.max_audio_minutes} minutes.`
          );
          setFile(null);
        }
      };
      audio.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        // Browser cannot decode duration directly; file will proceed to server-side check
      };
    } catch {
      // Audio element not supported
    }

    setErrorMessage(null);
    setFile(selectedFile);
  };

  const handleStartAnalysis = async (e) => {
    e.preventDefault();
    if (!file) {
      setErrorMessage('Please select an audio file to upload.');
      return;
    }

    // Enforce file checks again before any API call
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!limits.allowed_audio_extensions.includes(ext)) {
      setErrorMessage(`File type '${ext}' is not supported. Allowed formats: ${limits.allowed_audio_extensions.join(', ')}`);
      return;
    }
    if (file.size > limits.max_upload_bytes) {
      setErrorMessage(`File size exceeds maximum allowed limit of ${limits.max_upload_mb} MB.`);
      return;
    }

    setIsProcessing(true);
    setErrorMessage(null);
    setStatusMessage('Preparing upload...');

    try {
      let targetCounsellorId = counsellorId;

      // If user typed a new counsellor name, create it first
      if ((!targetCounsellorId || showAddCounsellor) && newCounsellorName.trim()) {
        setStatusMessage('Creating counsellor record...');
        const created = await apiJson('/counsellors', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newCounsellorName.trim() }),
        });
        targetCounsellorId = created.id;
      }

      setStatusMessage('Uploading recording & calculating checksum (SHA-256)...');
      const formData = new FormData();
      formData.append('file', file);
      formData.append('counsellor_id', targetCounsellorId || 1);
      formData.append('transcription_mode', transcriptionMode);

      const res = await apiFetch('/calls', {
        method: 'POST',
        body: formData,
      });

      const uploadedCall = await res.json();
      setStatusMessage('Upload complete! Pipeline analysis dispatched.');

      if (onCompleteUpload) {
        onCompleteUpload(uploadedCall);
      }
    } catch (err) {
      setErrorMessage(err.message || 'An error occurred during call upload.');
      setIsProcessing(false);
    }
  };

  return (
    <div style={{ maxWidth: '720px', margin: '0 auto' }}>
      <div className="console-panel">
        <div className="console-panel-header">
          <div>
            <span className="console-panel-title">Upload Counselling Audio Recording</span>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
              Audio is processed through speech-to-text diarization and verified against the QA rubric.
            </p>
          </div>
        </div>

        <form onSubmit={handleStartAnalysis} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Error Banner */}
          {errorMessage && (
            <div
              style={{
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid var(--danger)',
                borderRadius: 'var(--radius-md)',
                padding: '12px 16px',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                color: 'var(--danger-light)',
                fontSize: '13px',
              }}
            >
              <AlertCircle size={18} style={{ flexShrink: 0 }} />
              <div>{errorMessage}</div>
            </div>
          )}

          {/* Counsellor Selection */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <label style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Counsellor
              </label>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={() => setShowAddCounsellor(!showAddCounsellor)}
                style={{ fontSize: '11px', color: 'var(--primary)' }}
              >
                {showAddCounsellor ? 'Select existing' : 'Add new counsellor'}
              </button>
            </div>

            {showAddCounsellor ? (
              <input
                type="text"
                placeholder="Enter counsellor name..."
                value={newCounsellorName}
                onChange={(e) => setNewCounsellorName(e.target.value)}
                disabled={isProcessing}
                style={{
                  width: '100%',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '10px 14px',
                  color: 'var(--text-primary)',
                  fontSize: '13px',
                  outline: 'none',
                }}
              />
            ) : counsellors.length > 0 ? (
              <select
                value={counsellorId}
                onChange={(e) => setCounsellorId(Number(e.target.value))}
                disabled={isProcessing}
                style={{
                  width: '100%',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '10px 14px',
                  color: 'var(--text-primary)',
                  fontSize: '13px',
                  outline: 'none',
                }}
              >
                {counsellors.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} {c.email ? `(${c.email})` : ''}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                placeholder="Enter counsellor name (e.g. Counsellor 1)..."
                value={newCounsellorName}
                onChange={(e) => setNewCounsellorName(e.target.value)}
                disabled={isProcessing}
                style={{
                  width: '100%',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '10px 14px',
                  color: 'var(--text-primary)',
                  fontSize: '13px',
                  outline: 'none',
                }}
              />
            )}
          </div>

          {/* Transcription Language Selector (Section C.4) */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <Languages size={15} style={{ color: 'var(--primary)' }} />
              <label style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Transcription Language
              </label>
            </div>
            <select
              value={transcriptionMode}
              onChange={(e) => setTranscriptionMode(e.target.value)}
              disabled={isProcessing}
              style={{
                width: '100%',
                background: 'var(--bg-surface-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '10px 14px',
                color: 'var(--text-primary)',
                fontSize: '13px',
                outline: 'none',
              }}
            >
              <option value="auto">Auto (Recommended) — Automatically detect spoken language</option>
              <option value="hi">Hindi — Devanagari script output</option>
              <option value="hinglish">Hinglish — Conversational Hindi & English code-mixing</option>
              <option value="en">English — Standard English output</option>
            </select>
            <p style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '6px', marginBottom: 0 }}>
              {transcriptionMode === 'auto' && 'Sarvam STT automatically identifies Hindi, English, and regional accents.'}
              {transcriptionMode === 'hi' && 'Restricts STT vocabulary to Hindi and forces output in standard Devanagari script.'}
              {transcriptionMode === 'hinglish' && 'Optimized for bilingual calls where counsellor and student alternate between Hindi and English.'}
              {transcriptionMode === 'en' && 'Forces recognition into English vocabulary and Roman script.'}
            </p>
          </div>

          {/* Drag & Drop File Area */}
          <div
            onClick={() => !isProcessing && document.getElementById('audio-file-input').click()}
            style={{
              border: '1px dashed var(--border-medium)',
              borderRadius: 'var(--radius-md)',
              padding: '36px 20px',
              textAlign: 'center',
              background: 'rgba(0, 0, 0, 0.15)',
              cursor: isProcessing ? 'default' : 'pointer',
              transition: 'border-color var(--transition-fast)',
            }}
          >
            <input
              id="audio-file-input"
              type="file"
              accept={limits.allowed_audio_extensions.join(',')}
              style={{ display: 'none' }}
              onChange={(e) => {
                if (e.target.files && e.target.files[0]) {
                  validateAndSetFile(e.target.files[0]);
                }
              }}
              disabled={isProcessing}
            />
            <UploadCloud size={36} style={{ color: 'var(--primary)', margin: '0 auto 10px auto' }} />
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
              {file ? file.name : 'Drop call recording here or Browse files'}
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              Supports {limits.allowed_audio_extensions.join(', ').toUpperCase()} up to {limits.max_upload_mb} MB (max {limits.max_audio_minutes} min)
            </div>
          </div>

          {/* Processing Status Display */}
          {isProcessing && (
            <div
              style={{
                background: 'var(--bg-surface-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px 20px',
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
              }}
            >
              <RefreshCw size={16} className="status-dot online" style={{ animation: 'spin 1s linear infinite' }} />
              <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                {statusMessage}
              </div>
            </div>
          )}

          {/* Actions */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onCancel}
              disabled={isProcessing}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isProcessing || !file}
            >
              {isProcessing ? 'Uploading...' : 'Upload & Analyze'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
