import React, { useState } from 'react';
import { Loader2, Upload, Download } from 'lucide-react';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';
import { apiFetch } from '../lib/api';
import { getApiUrl } from '../config';

// ClipForge: the creator's own packaging around a source excerpt — intro /
// context card, voiceover, logo, source credit, outro (POST /api/clip/package).
const VOICE_MODES = [
    { value: 'intro', label: 'Intro' },
    { value: 'over', label: 'Over clip' },
    { value: 'outro', label: 'Outro' },
];
const CORNERS = [
    { value: 'top-left', label: '↖' },
    { value: 'top-right', label: '↗' },
    { value: 'bottom-left', label: '↙' },
    { value: 'bottom-right', label: '↘' },
];

async function uploadAsset(jobId, kind, file) {
    const form = new FormData();
    form.append('job_id', jobId);
    form.append('kind', kind);
    form.append('file', file);
    const res = await apiFetch('/api/clip/asset', { method: 'POST', body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `${kind} upload failed`);
    return body.asset;
}

export default function FinishShortModal({ isOpen, onClose, jobId, clipIndex, clip, inputFilename, onDone }) {
    const [title, setTitle] = useState(clip.topic || '');
    const [context, setContext] = useState('');
    const [outro, setOutro] = useState('');
    const [credit, setCredit] = useState('');
    const [useBrand, setUseBrand] = useState(true);
    const [logoFile, setLogoFile] = useState(null);
    const [corner, setCorner] = useState('top-right');
    const [voiceFile, setVoiceFile] = useState(null);
    const [voiceMode, setVoiceMode] = useState('intro');
    const [voiceOffset, setVoiceOffset] = useState(0);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [final, setFinal] = useState(clip.final || null);

    const render = async () => {
        setBusy(true);
        setError('');
        try {
            const logo = logoFile ? await uploadAsset(jobId, 'logo', logoFile) : null;
            const voice = voiceFile ? await uploadAsset(jobId, 'voiceover', voiceFile) : null;
            const res = await apiFetch('/api/clip/package', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: clipIndex,
                    input_filename: inputFilename || null,
                    intro_title: title,
                    intro_context: context,
                    // Empty fields fall back to the brand defaults in clipforge.config.yaml.
                    outro_text: outro || null,
                    credit_text: credit || null,
                    use_brand_logo: useBrand,
                    logo_asset: logo,
                    logo_corner: corner,
                    voiceover_asset: voice,
                    voiceover_mode: voiceMode,
                    voiceover_offset: Number(voiceOffset) || 0,
                }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || 'Packaging failed');
            setFinal(body.final);
            if (onDone) onDone(body.final);
        } catch (e) {
            setError(e.message || String(e));
        } finally {
            setBusy(false);
        }
    };

    const field = 'w-full bg-paper border border-rule rounded-input px-3 py-2 text-sm text-ink';
    const finalUrl = final?.url || (final?.file ? `/videos/${jobId}/${final.file}` : null);

    return (
        <Modal
            isOpen={isOpen}
            onClose={busy ? null : onClose}
            eyebrow="FINISHED SHORT"
            title="add your own touch"
            size="lg"
            footer={
                <div className="flex gap-3">
                    <button onClick={onClose} disabled={busy} className="btn-ghost flex-1 px-4 py-2 text-sm">Close</button>
                    <button onClick={render} disabled={busy} className="btn-primary flex-1 px-4 py-2 text-sm flex items-center justify-center gap-2">
                        {busy ? <><Loader2 size={14} className="animate-spin" /> rendering…</> : 'render finished short'}
                    </button>
                </div>
            }
        >
            <div className="space-y-4 text-sm">
                <p className="text-muted text-xs leading-snug">
                    The clip stays as it is; this makes a separate <b>final</b> file with your intro, voice,
                    branding and credit around it. Leave a field empty to use the defaults from
                    <code className="mx-1">clipforge.config.yaml</code>(branding).
                </p>
                <div className="grid sm:grid-cols-2 gap-3">
                    <label className="block">
                        <span className="eyebrow">INTRO / CONTEXT TITLE</span>
                        <input className={field} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Why small habits win" />
                    </label>
                    <label className="block">
                        <span className="eyebrow">CONTEXT LINE</span>
                        <input className={field} value={context} maxLength={160} onChange={(e) => setContext(e.target.value)} placeholder="e.g. A coach explains the 1-minute rule" />
                    </label>
                    <label className="block">
                        <span className="eyebrow">OUTRO / CALL TO ACTION</span>
                        <input className={field} value={outro} maxLength={120} onChange={(e) => setOutro(e.target.value)} placeholder="brand default" />
                    </label>
                    <label className="block">
                        <span className="eyebrow">SOURCE CREDIT</span>
                        <input className={field} value={credit} maxLength={160} onChange={(e) => setCredit(e.target.value)} placeholder="brand default, e.g. Source: Show (CC BY)" />
                    </label>
                </div>

                <div className="grid sm:grid-cols-2 gap-3">
                    <div>
                        <span className="eyebrow">YOUR VOICEOVER (optional)</span>
                        <label className="flex items-center gap-2 cursor-pointer btn-quiet px-3 py-2 text-xs">
                            <Upload size={14} /> {voiceFile ? voiceFile.name : 'choose audio (mp3, wav, m4a)'}
                            <input type="file" accept="audio/*" className="hidden" onChange={(e) => setVoiceFile(e.target.files?.[0] || null)} />
                        </label>
                        {voiceFile && (
                            <div className="mt-2 space-y-2">
                                <SegmentedControl options={VOICE_MODES} value={voiceMode} onChange={setVoiceMode} />
                                {voiceMode === 'over' && (
                                    <label className="block text-xs text-muted">
                                        starts at (s into the clip)
                                        <input type="number" min="0" step="0.5" className={field} value={voiceOffset} onChange={(e) => setVoiceOffset(e.target.value)} />
                                    </label>
                                )}
                            </div>
                        )}
                    </div>
                    <div>
                        <span className="eyebrow">LOGO</span>
                        <label className="flex items-center gap-2 cursor-pointer btn-quiet px-3 py-2 text-xs">
                            <Upload size={14} /> {logoFile ? logoFile.name : 'upload a PNG (or use brand logo)'}
                            <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => setLogoFile(e.target.files?.[0] || null)} />
                        </label>
                        <label className="flex items-center gap-2 mt-2 text-xs text-muted">
                            <input type="checkbox" checked={useBrand} onChange={(e) => setUseBrand(e.target.checked)} />
                            use brand logo from config when none uploaded
                        </label>
                        <div className="mt-2"><SegmentedControl options={CORNERS} value={corner} onChange={setCorner} /></div>
                    </div>
                </div>

                {error && <p className="text-warn text-xs">{error}</p>}

                {finalUrl && (
                    <div className="border-t border-rule pt-3 space-y-2">
                        <div className="flex items-center justify-between gap-2">
                            <span className="eyebrow">FINAL · QC {final?.qc?.status || '—'}</span>
                            <a href={getApiUrl(finalUrl)} download className="btn-quiet px-3 py-1.5 text-xs flex items-center gap-1">
                                <Download size={12} /> download final
                            </a>
                        </div>
                        <video src={getApiUrl(finalUrl)} controls className="w-full max-h-[50vh] rounded-input bg-black" />
                    </div>
                )}
            </div>
        </Modal>
    );
}
