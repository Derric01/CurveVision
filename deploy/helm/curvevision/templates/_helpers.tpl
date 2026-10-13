{{/* Names, labels and the environment every server container shares. */}}

{{- define "curvevision.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "curvevision.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "curvevision.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "curvevision.secretName" -}}
{{- default (printf "%s-secrets" (include "curvevision.fullname" .)) .Values.existingSecret -}}
{{- end -}}

{{- define "curvevision.serverImage" -}}
{{ .Values.image.server.repository }}:{{ default .Chart.AppVersion .Values.image.server.tag }}
{{- end -}}

{{- define "curvevision.webImage" -}}
{{ .Values.image.web.repository }}:{{ default .Chart.AppVersion .Values.image.web.tag }}
{{- end -}}

{{/* Required values, checked once, with what to do about them. */}}
{{- define "curvevision.validate" -}}
{{- if not .Values.existingSecret -}}
{{- if not .Values.secretKey -}}
{{- fail "Set secretKey (python3 -c 'import secrets; print(secrets.token_urlsafe(48))'), or existingSecret." -}}
{{- end -}}
{{- if not .Values.database.url -}}
{{- fail "Set database.url to a PostgreSQL URL (postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE), or existingSecret." -}}
{{- end -}}
{{- if not .Values.redis.url -}}
{{- fail "Set redis.url (redis://HOST:6379/0): the job broker and the shared rate limit. Or existingSecret." -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Environment for every server container: settings from the ConfigMap, secrets by key. */}}
{{- define "curvevision.serverEnv" -}}
- name: CURVEVISION_SECRET_KEY
  valueFrom: {secretKeyRef: {name: {{ include "curvevision.secretName" . }}, key: secret-key}}
- name: CURVEVISION_DATABASE_URL
  valueFrom: {secretKeyRef: {name: {{ include "curvevision.secretName" . }}, key: database-url}}
- name: CURVEVISION_REDIS_URL
  valueFrom: {secretKeyRef: {name: {{ include "curvevision.secretName" . }}, key: redis-url}}
- name: CURVEVISION_S3_ACCESS_KEY_ID
  valueFrom: {secretKeyRef: {name: {{ include "curvevision.secretName" . }}, key: s3-access-key-id, optional: true}}
- name: CURVEVISION_S3_SECRET_ACCESS_KEY
  valueFrom: {secretKeyRef: {name: {{ include "curvevision.secretName" . }}, key: s3-secret-access-key, optional: true}}
{{- end -}}
