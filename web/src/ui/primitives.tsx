/**
 * The CurveVision design-system primitives.
 *
 * Deliberately hand-built rather than adopted from a component library: an annotation tool
 * lives or dies on the feel of a dozen controls, and inheriting someone else's look would
 * make the product indistinguishable from every other tool built on the same library.
 * Fifteen small components is a small price for an identity.
 */

import clsx from 'clsx';
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md';

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-curve-500 text-ink-950 hover:bg-curve-400 font-medium',
  secondary: 'bg-ink-800 text-ink-100 hover:bg-ink-700 border border-ink-700',
  ghost: 'text-ink-300 hover:bg-ink-800 hover:text-ink-100',
  danger: 'bg-red-500/90 text-white hover:bg-red-500',
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  active?: boolean;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  active = false,
  className,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      className={clsx(
        'inline-flex items-center justify-center rounded-md transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-40',
        BUTTON_SIZES[size],
        BUTTON_VARIANTS[variant],
        active && 'ring-1 ring-curve-400',
        className,
      )}
      {...props}
    />
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export function Input({ label, hint, error, className, id, ...props }: InputProps) {
  const inputId = id ?? props.name;
  return (
    <label className="block" htmlFor={inputId}>
      {label && <span className="mb-1.5 block text-xs font-medium text-ink-300">{label}</span>}
      <input
        id={inputId}
        className={clsx(
          'h-9 w-full rounded-md border bg-ink-950 px-3 text-sm text-ink-100',
          'placeholder:text-ink-500',
          error ? 'border-red-500/70' : 'border-ink-700 focus:border-curve-400',
          className,
        )}
        {...props}
      />
      {error ? (
        <span className="mt-1 block text-xs text-red-400">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-xs text-ink-500">{hint}</span>
      ) : null}
    </label>
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx('panel', className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between border-b border-ink-800 px-4 py-2.5">
          <h2 className="text-sm font-medium text-ink-200">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const BADGE_TONES = {
  neutral: 'bg-ink-800 text-ink-300 border-ink-700',
  info: 'bg-sky-500/10 text-sky-300 border-sky-500/30',
  success: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  warning: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  danger: 'bg-red-500/10 text-red-300 border-red-500/30',
  accent: 'bg-curve-500/10 text-curve-300 border-curve-500/30',
} as const;

export type BadgeTone = keyof typeof BADGE_TONES;

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium',
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Maps a job state to a badge tone, so state colour is consistent everywhere. */
export function jobStateTone(state: string): BadgeTone {
  switch (state) {
    case 'accepted':
      return 'success';
    case 'rejected':
      return 'danger';
    case 'submitted':
      return 'warning';
    case 'in_progress':
      return 'info';
    default:
      return 'neutral';
  }
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={clsx(
        'inline-block h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-curve-400',
        className,
      )}
    />
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      {icon && <div className="text-ink-600">{icon}</div>}
      <div>
        <p className="text-sm font-medium text-ink-200">{title}</p>
        {description && <p className="mt-1 max-w-md text-sm text-ink-500">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div
      className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-ink-800', className)}
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full bg-curve-500 transition-[width] duration-300"
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  const message =
    error instanceof Error ? error.message : typeof error === 'string' ? error : 'Request failed';
  return (
    <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      {message}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}
