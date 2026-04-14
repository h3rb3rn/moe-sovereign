#!/usr/bin/env bash
# compile.sh — Kompiliert alle LaTeX-Dokumente des Sovereign MoE Projekts
#
# Verwendung:
#   ./compile.sh              # alle Dokumente
#   ./compile.sh handbook     # nur handbook
#   ./compile.sh whitepaper   # nur whitepaper
#   ./compile.sh best-practices  # nur best-practices
#
# Ausgabe: docs/build/handbook.pdf, whitepaper.pdf, best-practices.pdf
#
# Anforderungen: pdflatex, bibtex (z.B. via texlive-full oder miktex)

set -euo pipefail

# ── Farben ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ── Pfade ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
PDFLATEX="pdflatex -interaction=nonstopmode -halt-on-error"

mkdir -p "${BUILD_DIR}"

# ── Hilfsfunktionen ──────────────────────────────────────────────────────────
log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${BOLD}══════════════════════════════════════${NC}"; echo -e "${BOLD} $*${NC}"; echo -e "${BOLD}══════════════════════════════════════${NC}"; }

show_log_tail() {
    local logfile="$1"
    if [[ -f "${logfile}" ]]; then
        echo -e "\n${YELLOW}── Letzte 30 Zeilen von ${logfile} ──${NC}"
        tail -n 30 "${logfile}" | grep -E "^!" --color=always || tail -n 30 "${logfile}"
    fi
}

# ── Kompilierungsfunktionen ───────────────────────────────────────────────────

# compile_simple <docdir> <outname>
# Führt pdflatex zweimal aus (für Querverweise), kein bibtex.
compile_simple() {
    local docdir="${SCRIPT_DIR}/$1"
    local outname="$2"

    log_section "Kompiliere: ${outname}"

    if [[ ! -d "${docdir}" ]]; then
        log_error "Verzeichnis nicht gefunden: ${docdir}"
        return 1
    fi
    if [[ ! -f "${docdir}/main.tex" ]]; then
        log_error "main.tex nicht gefunden in: ${docdir}"
        return 1
    fi

    pushd "${docdir}" > /dev/null

    log_info "Pass 1/2 — pdflatex ${outname}..."
    if ! ${PDFLATEX} main.tex > /dev/null 2>&1; then
        log_error "pdflatex Pass 1 fehlgeschlagen"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    log_info "Pass 2/2 — pdflatex ${outname} (Querverweise)..."
    if ! ${PDFLATEX} main.tex > /dev/null 2>&1; then
        log_error "pdflatex Pass 2 fehlgeschlagen"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    if [[ -f "main.pdf" ]]; then
        cp "main.pdf" "${BUILD_DIR}/${outname}.pdf"
        log_ok "${outname}.pdf → ${BUILD_DIR}/${outname}.pdf"
    else
        log_error "main.pdf wurde nicht erzeugt"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    popd > /dev/null
    return 0
}

# compile_with_bib <docdir> <outname>
# Führt pdflatex → bibtex → pdflatex → pdflatex aus (für Literaturverzeichnis).
compile_with_bib() {
    local docdir="${SCRIPT_DIR}/$1"
    local outname="$2"

    log_section "Kompiliere mit Bibliographie: ${outname}"

    if [[ ! -d "${docdir}" ]]; then
        log_error "Verzeichnis nicht gefunden: ${docdir}"
        return 1
    fi
    if [[ ! -f "${docdir}/main.tex" ]]; then
        log_error "main.tex nicht gefunden in: ${docdir}"
        return 1
    fi

    pushd "${docdir}" > /dev/null

    log_info "Pass 1/4 — pdflatex ${outname} (initial)..."
    if ! ${PDFLATEX} main.tex > /dev/null 2>&1; then
        log_error "pdflatex Pass 1 fehlgeschlagen"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    log_info "Pass 2/4 — bibtex (Literaturverzeichnis)..."
    if ! bibtex main > /dev/null 2>&1; then
        log_warn "bibtex meldete Warnungen (Ausgabe weiter unten)"
        if [[ -f "main.blg" ]]; then
            echo -e "${YELLOW}── bibtex-Log ──${NC}"
            tail -20 "main.blg"
        fi
        # bibtex-Fehler sind oft nicht fatal — weiter
    fi

    log_info "Pass 3/4 — pdflatex ${outname} (Referenzen einfügen)..."
    if ! ${PDFLATEX} main.tex > /dev/null 2>&1; then
        log_error "pdflatex Pass 3 fehlgeschlagen"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    log_info "Pass 4/4 — pdflatex ${outname} (Referenzen finalisieren)..."
    if ! ${PDFLATEX} main.tex > /dev/null 2>&1; then
        log_error "pdflatex Pass 4 fehlgeschlagen"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    if [[ -f "main.pdf" ]]; then
        cp "main.pdf" "${BUILD_DIR}/${outname}.pdf"
        log_ok "${outname}.pdf → ${BUILD_DIR}/${outname}.pdf"
    else
        log_error "main.pdf wurde nicht erzeugt"
        show_log_tail "main.log"
        popd > /dev/null
        return 1
    fi

    popd > /dev/null
    return 0
}

# ── Hauptlogik ───────────────────────────────────────────────────────────────

TARGET="${1:-all}"
ERRORS=0

case "${TARGET}" in
    handbook)
        compile_simple "handbook" "handbook" || ERRORS=$((ERRORS + 1))
        ;;
    whitepaper)
        compile_with_bib "whitepaper" "whitepaper" || ERRORS=$((ERRORS + 1))
        ;;
    best-practices)
        compile_simple "best-practices" "best-practices" || ERRORS=$((ERRORS + 1))
        ;;
    all)
        compile_simple "handbook" "handbook"          || ERRORS=$((ERRORS + 1))
        compile_with_bib "whitepaper" "whitepaper"    || ERRORS=$((ERRORS + 1))
        compile_simple "best-practices" "best-practices" || ERRORS=$((ERRORS + 1))
        ;;
    *)
        echo "Verwendung: $0 [handbook|whitepaper|best-practices|all]"
        exit 1
        ;;
esac

# ── Zusammenfassung ──────────────────────────────────────────────────────────
echo ""
log_section "Ergebnis"

for pdf in handbook whitepaper best-practices; do
    if [[ -f "${BUILD_DIR}/${pdf}.pdf" ]]; then
        size=$(du -h "${BUILD_DIR}/${pdf}.pdf" | cut -f1)
        log_ok "${pdf}.pdf (${size})"
    fi
done

if [[ ${ERRORS} -eq 0 ]]; then
    echo -e "\n${GREEN}${BOLD}Alle Dokumente erfolgreich kompiliert.${NC}"
    echo -e "Ausgabeverzeichnis: ${BUILD_DIR}\n"
    exit 0
else
    echo -e "\n${RED}${BOLD}${ERRORS} Dokument(e) fehlgeschlagen.${NC}\n"
    exit 1
fi
