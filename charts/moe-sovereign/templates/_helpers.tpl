{{/*
Common helpers for moe-sovereign chart.
*/}}

{{- define "moe.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "moe.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "moe.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "moe.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "moe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: moe-sovereign
moe.sovereign/profile: {{ .Values.profile | quote }}
{{- end -}}

{{- define "moe.selectorLabels" -}}
app.kubernetes.io/name: {{ include "moe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "moe.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "moe.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Image tag fallback — uses Chart.AppVersion when values.*.tag is empty.
*/}}
{{- define "moe.image.orchestrator" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s/%s:%s" .Values.image.registry .Values.image.repository $tag -}}
{{- end -}}

{{- define "moe.image.mcp" -}}
{{- $tag := .Values.mcp.image.tag | default .Chart.AppVersion -}}
{{- printf "%s/%s:%s" .Values.image.registry .Values.mcp.image.repository $tag -}}
{{- end -}}

{{- define "moe.image.admin" -}}
{{- $tag := .Values.admin.image.tag | default .Chart.AppVersion -}}
{{- printf "%s/%s:%s" .Values.image.registry .Values.admin.image.repository $tag -}}
{{- end -}}

{{/*
Connection-string resolvers — prefer in-cluster subchart services when
enabled, fall back to the external URL supplied by the operator.
*/}}
{{- define "moe.postgresUrl" -}}
{{- if .Values.postgresql.enabled -}}
postgres://{{ .Values.postgresql.auth.username }}@{{ .Release.Name }}-postgresql:5432/{{ .Values.postgresql.auth.database }}
{{- else -}}
{{ .Values.external.postgresUrl }}
{{- end -}}
{{- end -}}

{{- define "moe.kafkaUrl" -}}
{{- if .Values.kafka.enabled -}}
{{ .Release.Name }}-kafka:9092
{{- else -}}
{{ .Values.external.kafkaUrl }}
{{- end -}}
{{- end -}}

{{- define "moe.valkeyUrl" -}}
{{- if .Values.valkey.enabled -}}
redis://{{ .Release.Name }}-valkey-master:6379/0
{{- else -}}
{{ .Values.external.valkeyUrl }}
{{- end -}}
{{- end -}}

{{- define "moe.neo4jUri" -}}
{{- if .Values.neo4j.enabled -}}
bolt://{{ .Release.Name }}-neo4j:7687
{{- else -}}
{{ .Values.external.neo4jUri }}
{{- end -}}
{{- end -}}

{{/*
Is this an OpenShift-targeted install? Detected either explicitly via
.Values.openshift.enabled OR by the presence of the route.openshift.io API.
*/}}
{{- define "moe.isOpenShift" -}}
{{- if .Values.openshift.enabled -}}true{{- else if .Capabilities.APIVersions.Has "route.openshift.io/v1" -}}true{{- else -}}false{{- end -}}
{{- end -}}
