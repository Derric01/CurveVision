/**
 * Bring existing annotations into this task: a COCO, CVAT XML, YOLO, Pascal VOC, KITTI,
 * MOTChallenge or CurveVision archive. The rules are in `annotationImport.ts`.
 */

import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileUp } from 'lucide-react';

import { api } from '@/api/client';
import type { AnnotationImportResult } from '@/api/types';
import { Button, ErrorNotice, Panel } from '@/ui/primitives';
import { REPLACE_WARNING, canImport, describeImport, importableFormats } from './annotationImport';

export function AnnotationImportPanel({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const formats = useQuery({ queryKey: ['formats'], queryFn: () => api.formats() });
  const importable = useMemo(() => importableFormats(formats.data), [formats.data]);

  const [format, setFormat] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [policy, setPolicy] = useState<'append' | 'replace'>('append');
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [result, setResult] = useState<AnnotationImportResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const chosen = format || importable[0]?.id || '';

  const run = useMutation({
    mutationFn: () =>
      api.importAnnotations(taskId, file!, { format: chosen, conflictPolicy: policy }),
    onSuccess: (imported) => {
      setResult(imported);
      setFile(null);
      setReplaceConfirmed(false);
      if (fileInput.current) fileInput.current.value = '';
      // Counts, progress and every open editor's copy of these jobs have moved.
      void queryClient.invalidateQueries({ queryKey: ['task', taskId] });
      void queryClient.invalidateQueries({ queryKey: ['jobs', taskId] });
      void queryClient.invalidateQueries({ queryKey: ['annotations'] });
      void queryClient.invalidateQueries({ queryKey: ['project'] });
      void queryClient.invalidateQueries({ queryKey: ['labels'] });
    },
  });

  const ready = canImport({ format: chosen, file, policy, replaceConfirmed });

  return (
    <Panel title="Import annotations">
      <div className="space-y-3" data-import-panel="">
        <p className="text-xs text-ink-500">
          Bring in labels made elsewhere — in CVAT, a COCO dataset, YOLO files. Frames are
          matched by image file name, or by frame number where the format numbers them
          (MOTChallenge, CVAT tracks). Tracks, and attributes the project declares, come too.
        </p>
        <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-300">Format</span>
            <select
              className="h-9 w-full rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
              value={chosen}
              onChange={(event) => setFormat(event.target.value)}
              data-import-format=""
            >
              {importable.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-300">Archive (.zip)</span>
            <input
              ref={fileInput}
              type="file"
              accept=".zip,application/zip"
              className="block w-full text-sm text-ink-300 file:mr-3 file:rounded file:border-0 file:bg-ink-800 file:px-3 file:py-1.5 file:text-ink-100"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setResult(null);
              }}
              data-import-file=""
            />
          </label>
        </div>

        <fieldset className="space-y-1.5 text-sm text-ink-300">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="import-policy"
              checked={policy === 'append'}
              onChange={() => setPolicy('append')}
              data-import-append=""
            />
            Add to what is already here
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="import-policy"
              checked={policy === 'replace'}
              onChange={() => setPolicy('replace')}
              data-import-replace=""
            />
            Replace what is already here
          </label>
        </fieldset>
        {policy === 'replace' && (
          <label
            className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
            data-import-replace-warning=""
          >
            <input
              type="checkbox"
              className="mt-0.5"
              checked={replaceConfirmed}
              onChange={(event) => setReplaceConfirmed(event.target.checked)}
              data-import-replace-confirm=""
            />
            {REPLACE_WARNING}
          </label>
        )}

        {run.error && <ErrorNotice error={run.error} />}
        <div className="flex justify-end">
          <Button
            variant="primary"
            disabled={!ready || run.isPending}
            onClick={() => run.mutate()}
            data-import-run=""
          >
            <FileUp size={14} className="mr-1.5" />
            {run.isPending ? 'Importing…' : 'Import'}
          </Button>
        </div>

        {result && (
          <div className="space-y-1.5 border-t border-ink-800 pt-3" data-import-result="">
            <p
              className={
                result.frames_matched === 0 ? 'text-sm text-amber-300' : 'text-sm text-emerald-300'
              }
              data-import-summary=""
            >
              {describeImport(result)}
            </p>
            {result.warnings.length > 0 && (
              <ul className="space-y-1 text-xs text-amber-300" data-import-warnings="">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}
