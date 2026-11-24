import React, { useEffect, useState } from 'react';
import { Loader2, ExternalLink } from 'lucide-react';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';
import { apiFetch } from '../lib/api';

// ClipForge: direct upload to the creator's own channel (YouTube Data API).
// Private by default; scheduled/public needs a source-rights record.
const PRIVACY = [
    { value: 'private', label: 'Private' },
    { value: 'unlisted', label: 'Unlisted' },
    { value: 'public', label: 'Public' },
];
const RIGHTS = [
    { value: 'own', label: 'I own it' },
    { value: 'licensed', label: 'Licensed / permission' },
    { value: 'cc', label: 'Creative Commons' },
    { value: 'public_domain', label: 'Public domain' },
    { value: 'unclear', label: 'Not sure' },
];

async function json(path, body) {
    const res = await apiFetch(path, body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `request failed (${res.status})`);
    return data;
}

export default function YouTubePublishModal({ isOpen, onClose, jobId, clipIndex, clip, inputFilename, onUploaded }) {
    const [status, setStatus] = useState(null);
    const [rights, setRights] = useState(null);
    const [rightsForm, setRightsForm] = useState({ category: 'unclear', license: '', attribution: '' });
    const [draft, setDraft] = useState(null);
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [tags, setTags] = useState('');
    const [privacy, setPrivacy] = useState('private');
    const [when, setWhen] = useState('');
    const [useFinal, setUseFinal] = useState(true);
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    const [result, setResult] = useState(clip.youtube || null);

    useEffect(() => {
        if (!isOpen) return;
        let live = true;
        (async () => {
            try {
                const [st, rg] = await Promise.all([
                    json('/api/youtube/status'),
                    json(`/api/job/${encodeURIComponent(jobId)}/rights`),
                ]);
                if (!live) return;
                setStatus(st);
                setPrivacy(st.privacy || 'private');
                setRights(rg);
                if (rg.rights) setRightsForm({ category: rg.rights.category, license: rg.rights.license || '', attribution: rg.rights.attribution || '' });
                setBusy('drafting');
                const d = await json('/api/youtube/metadata', { job_id: jobId, clip_index: clipIndex });
                if (!live) return;
                setDraft(d);
                setTitle(d.title || '');
                setDescription(d.description || '');
                setTags((d.tags || []).join(', '));
            } catch (e) {
                if (live) setError(e.message);
            } finally {
                if (live) setBusy('');
            }
        })();
        return () => { live = false; };
    }, [isOpen, jobId, clipIndex]);

    const saveRights = async () => {
        setBusy('rights'); setError('');
        try {
            const r = await json('/api/job/rights', { job_id: jobId, ...rightsForm });
            setRights(r);
        } catch (e) { setError(e.message); } finally { setBusy(''); }
    };

    const upload = async () => {
        setBusy('uploading'); setError('');
        try {
            const r = await json('/api/youtube/upload', {
                job_id: jobId, clip_index: clipIndex, input_filename: inputFilename || null,
                use_final: useFinal, title, description,
                tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
                privacy, publish_at: when ? new Date(when).toISOString() : null,
            });
            setResult(r.youtube);
            if (onUploaded) onUploaded(r.youtube);
        } catch (e) { setError(e.message); } finally { setBusy(''); }
    };

    const field = 'w-full bg-paper border border-rule rounded-input px-3 py-2 text-sm text-ink';
    const needsRights = (privacy === 'public' || when) && !rights?.public_allowed;

    return (
        <Modal isOpen={isOpen} onClose={busy === 'uploading' ? null : onClose} eyebrow="YOUTUBE" title="upload to your channel" size="lg"
            footer={
                <div className="flex gap-3">
                    <button onClick={onClose} disabled={busy === 'uploading'} className="btn-ghost flex-1 px-4 py-2 text-sm">Close</button>
                    <button onClick={upload} disabled={!!busy || !status?.connected || !title || needsRights}
                        className="btn-primary flex-1 px-4 py-2 text-sm flex items-center justify-center gap-2">
                        {busy === 'uploading' ? <><Loader2 size={14} className="animate-spin" /> uploading…</> : (when ? 'schedule upload' : 'upload')}
                    </button>
                </div>
            }>
            <div className="space-y-4 text-sm">
                {status && !status.connected && (
                    <p className="text-warn text-xs">
                        YouTube is not connected. Set <code>youtube.oauth_client_file</code> in clipforge.config.yaml and run
                        <code className="mx-1">python youtube_publisher.py auth</code> once (see the user guide).
                    </p>
                )}
                {status?.connected && (
                    <p className="text-xs text-muted">
                        Channel: <b className="text-ink">{status.channel || 'connected'}</b> · uploads today {status.uploads_today}/{status.max_per_day}
                    </p>
                )}

                <div className="border border-rule rounded-input p-3 space-y-2">
                    <span className="eyebrow">SOURCE RIGHTS (your declaration, applies to this whole video)</span>
                    <div className="grid sm:grid-cols-3 gap-2">
                        <select className={field} value={rightsForm.category} onChange={(e) => setRightsForm({ ...rightsForm, category: e.target.value })}>
                            {RIGHTS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                        </select>
                        <input className={field} placeholder="licence, e.g. CC BY 4.0" value={rightsForm.license} onChange={(e) => setRightsForm({ ...rightsForm, license: e.target.value })} />
                        <input className={field} placeholder="attribution / credit" value={rightsForm.attribution} onChange={(e) => setRightsForm({ ...rightsForm, attribution: e.target.value })} />
                    </div>
                    <div className="flex items-center justify-between gap-2">
                        <span className={`text-xs ${rights?.public_allowed ? 'text-ok' : 'text-muted'}`}>
                            {rights?.public_allowed ? 'public / scheduled publishing allowed' : (rights?.reason || 'private or unlisted only')}
                        </span>
                        <button onClick={saveRights} disabled={!!busy} className="btn-quiet px-3 py-1 text-xs">save rights</button>
                    </div>
                </div>

                {busy === 'drafting' && <p className="text-xs text-muted flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> drafting title and description from the clip…</p>}
                {draft?.titles?.length > 1 && (
                    <div className="space-y-1">
                        <span className="eyebrow">TITLE OPTIONS</span>
                        {draft.titles.map((t) => (
                            <label key={t} className="flex items-start gap-2 text-xs cursor-pointer">
                                <input type="radio" name="yt-title" checked={title === t} onChange={() => setTitle(t)} /> {t}
                            </label>
                        ))}
                    </div>
                )}
                <label className="block"><span className="eyebrow">TITLE</span>
                    <input className={field} value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} /></label>
                <label className="block"><span className="eyebrow">DESCRIPTION</span>
                    <textarea className={`${field} h-28`} value={description} maxLength={4900} onChange={(e) => setDescription(e.target.value)} /></label>
                <label className="block"><span className="eyebrow">TAGS (comma separated)</span>
                    <input className={field} value={tags} onChange={(e) => setTags(e.target.value)} /></label>

                <div className="grid sm:grid-cols-2 gap-3 items-end">
                    <div><span className="eyebrow">PRIVACY</span><SegmentedControl options={PRIVACY} value={privacy} onChange={setPrivacy} /></div>
                    <label className="block"><span className="eyebrow">SCHEDULE (optional)</span>
                        <input type="datetime-local" className={field} value={when} onChange={(e) => setWhen(e.target.value)} /></label>
                </div>
                {clip.final?.file && (
                    <label className="flex items-center gap-2 text-xs text-muted">
                        <input type="checkbox" checked={useFinal} onChange={(e) => setUseFinal(e.target.checked)} />
                        upload the finished short (intro/branding) instead of the plain clip
                    </label>
                )}
                {(clip.end - clip.start) > 60 && rights?.rights?.category !== 'own' && (
                    <p className="text-xs text-brass">
                        Over 60 s from someone else's video: if a Content ID claim is made, a Short this long is blocked worldwide.
                    </p>
                )}
                {needsRights && <p className="text-xs text-warn">Public or scheduled publishing needs a saved rights record that allows it.</p>}
                {error && <p className="text-xs text-warn">{error}</p>}
                {result?.video_id && (
                    <p className="text-xs text-ok flex items-center gap-1">
                        Uploaded ({result.privacy}{result.publish_at ? `, publishes ${new Date(result.publish_at).toLocaleString()}` : ''}):
                        <a href={result.url} target="_blank" rel="noreferrer" className="underline flex items-center gap-1">{result.url} <ExternalLink size={11} /></a>
                    </p>
                )}
            </div>
        </Modal>
    );
}
