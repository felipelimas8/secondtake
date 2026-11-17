"""Traduções do Second Take (português e inglês).

Uso:
    from .i18n import t, L
    t('key', n=3)        -> texto já traduzido no idioma atual
    L('key', n=3)        -> mensagem "preguiçosa": guarda a chave e só traduz
                            quando é exibida, então troca de idioma na hora

O idioma vem, nesta ordem, de: SECONDTAKE_LANG, preferência salva pelo app,
idioma do sistema (macOS: AppleLanguages) e, por fim, português.
"""
from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
import threading

LANGS = ('pt', 'en')
LANG_NAMES = {'pt': 'Português', 'en': 'English'}
_lang = 'pt'


# ---------------------------------------------------------------- catálogo
# chave: (português, inglês)
CATALOG: dict[str, tuple[str, str]] = {
    # ---- status / modos
    'status.OK': ('Íntegro', 'Intact'),
    'status.CABECALHO_DANIFICADO': ('Cabeçalho', 'Header'),
    'status.SEM_MOOV': ('Sem índice', 'No index'),
    'status.VAZIO': ('Vazio', 'Empty'),
    'status.DESCONHECIDO': ('Desconhecido', 'Unknown'),
    'mode.cabecalho': ('Reparar', 'Repair'),
    'mode.reconstruir': ('Reconstruir', 'Rebuild'),
    'mode.extrair': ('Extrair', 'Extract'),

    # ---- diagnóstico (core)
    'diag.empty_file': ('arquivo com 0 bytes', 'file is 0 bytes'),
    'diag.zero_start': ('início zerado', 'start of file is zeroed'),
    'diag.rsv': ('arquivo RSV: gravação interrompida, sem índice (moov)',
                 'RSV file: interrupted recording, no index (moov)'),
    'diag.no_moov': ('índice (moov) ausente — gravação interrompida',
                     'index (moov) missing — interrupted recording'),
    'diag.bad_moov': ('moov ilegível: {err}', 'unreadable moov: {err}'),
    'diag.moov_out': ('moov aponta para fora dos dados', 'moov points outside the data'),
    'diag.header': ('cabeçalho corrompido, índice íntegro — reparável',
                    'corrupted header, intact index — repairable'),
    'diag.header_zero': ('início zerado, índice íntegro — reparável',
                         'zeroed start, intact index — repairable'),
    'diag.header_manual': ('cabeçalho corrompido, índice íntegro — reparável (referência manual)',
                           'corrupted header, intact index — repairable (manual reference)'),
    'diag.silent': ('áudio em silêncio na gravação original', 'audio was already silent when recorded'),
    'diag.no_ref': ('sem clipe de referência válido na pasta', 'no valid reference clip in the folder'),
    'diag.error': ('erro: {err}', 'error: {err}'),
    'diag.rebuild': ('reconstrução: {secs} s recuperáveis ({chunks} blocos{extra}) — {reason}',
                     'rebuild: {secs} s recoverable ({chunks} chunks{extra}) — {reason}'),
    'diag.rebuild_gaps': ('; {n} trecho(s) danificado(s) pulado(s), {mb} MB',
                          '; {n} damaged section(s) skipped, {mb} MB'),
    'diag.rebuild_lost': ('; {mb} MB finais descartados', '; last {mb} MB discarded'),
    'diag.rebuild_failed': ('reconstrução falhou ({err})', 'rebuild failed ({err})'),
    'diag.extract': ('extração bruta: vídeo .h264 (+ MP4 limpo)', 'raw extraction: .h264 video (+ clean MP4)'),
    'diag.extract_full': ('layout reconhecido: vídeo e áudio completos',
                          'layout recognized: full video and audio'),
    'diag.extract_video': ('layout não reconhecido: só vídeo', 'layout not recognized: video only'),
    'diag.sep': (' — ', ' — '),

    # ---- motivos de parada da varredura (rebuild)
    'reason.eof': ('fim do arquivo', 'end of file'),
    'reason.end': ('fim da gravação', 'end of recording'),
    'reason.cut': ('gravação cortada no último bloco', 'recording cut in the last chunk'),
    'reason.no_good_after': ('sem blocos íntegros após {pos}', 'no intact chunks after {pos}'),
    'reason.audio_incomplete': ('último bloco de áudio incompleto', 'last audio chunk incomplete'),
    'reason.no_aud': ('vídeo não começa com AUD em {pos}', 'video does not start with an AUD at {pos}'),
    'reason.eof_video': ('fim dos dados no meio do vídeo', 'data ends in the middle of the video'),
    'reason.bad_nal': ('NAL inválido em {pos}', 'invalid NAL at {pos}'),
    'reason.too_many': ('mais de {g} quadros no bloco em {pos}', 'more than {g} frames in the chunk at {pos}'),

    # ---- erros (rebuild/core)
    'err.ref_tracks': ('referência sem trilhas de vídeo e áudio', 'reference has no video and audio tracks'),
    'err.ref_layout': ('cabeçalho da referência fora do padrão XAVC S',
                       'reference header does not match the XAVC S layout'),
    'err.no_compatible': ('nenhuma referência compatível', 'no compatible reference'),
    'err.offset_mismatch': ('offset de dados diferente do cabeçalho da referência',
                            'data offset differs from the reference header'),
    'err.ref_mdat': ('referência sem mdat de 64 bits', 'reference has no 64-bit mdat'),
    'err.not_repairable': ('{name}: status {status} não é reparável', '{name}: status {status} is not repairable'),
    'err.no_ref': ('{name}: sem referência', '{name}: no reference'),
    'err.no_ref_cli': ('sem referência; use --ref', 'no reference; use --ref'),

    # ---- verificação
    'verify.no_ffmpeg': ('ffmpeg não encontrado — verificação pulada', 'ffmpeg not found — verification skipped'),
    'verify.no_ffprobe': ('ffprobe não encontrado — verificação pulada', 'ffprobe not found — verification skipped'),
    'verify.frames': ('{read}/{total} quadros decodificados', '{read}/{total} frames decoded'),
    'verify.lost': (' ({n} perdidos no início)', ' ({n} lost at the start)'),
    'verify.fail': ('falha ao decodificar o vídeo', 'video failed to decode'),
    'verify.audio_peak': (' · áudio pico {db} dBFS', ' · audio peak {db} dBFS'),
    'verify.extracted': ('{n} quadros extraídos', '{n} frames extracted'),
    'verify.extracted_audio': (', {s} s de áudio', ', {s} s of audio'),
    'verify.extracted_tc': (', timecode {tc}', ', timecode {tc}'),
    'verify.extracted_noaudio': (', sem áudio', ', no audio'),
    'verify.nothing': ('nenhum quadro H.264 válido encontrado — sem recuperação possível',
                       'no valid H.264 frames found — nothing recoverable'),
    'verify.timeout': ('verificação cancelada: o ffprobe passou de {s} s sem responder (arquivo salvo, confira num player)',
                       'verification cancelled: ffprobe took over {s} s without answering (file saved, check it in a player)'),
    'verify.cancelled': ('cancelado: nada foi gravado para este clipe', 'cancelled: nothing was written for this clip'),
    'verify.error': ('ERRO: {err}', 'ERROR: {err}'),

    # ---- relatório CSV
    'csv.name': ('arquivo', 'file'),
    'csv.status': ('status', 'status'),
    'csv.status_label': ('situação', 'state'),
    'csv.action': ('ação', 'action'),
    'csv.detail': ('diagnóstico', 'diagnosis'),
    'csv.camera': ('câmera', 'camera'),
    'csv.codec': ('codec', 'codec'),
    'csv.fps': ('fps', 'fps'),
    'csv.size': ('bytes', 'bytes'),
    'csv.audio_db': ('áudio_pico_dbfs', 'audio_peak_dbfs'),
    'csv.reference': ('referência', 'reference'),
    'csv.output': ('saída', 'output'),
    'csv.verify': ('verificação', 'verification'),
    'csv.path': ('caminho', 'path'),
    'csv.report_name': ('relatorio_reparo.csv', 'repair_report.csv'),
    'csv.diag_name': ('diagnostico_secondtake.csv', 'secondtake_diagnosis.csv'),

    'csv.resolution': ('resolução', 'resolution'),
    'csv.video': ('vídeo', 'video'),
    'csv.duration': ('duração_s', 'duration_s'),
    'csv.bitrate': ('bitrate_video_mbps', 'video_bitrate_mbps'),
    'csv.color': ('cor', 'color'),
    'csv.audio': ('áudio', 'audio'),
    'csv.timecode': ('timecode', 'timecode'),
    'csv.created': ('gravado_em', 'recorded_at'),

    # ---- metadados
    'meta.title': ('Detalhes do clipe', 'Clip details'),
    'meta.camera': ('Câmera', 'Camera'),
    'meta.created': ('Gravado em', 'Recorded'),
    'meta.timecode': ('Timecode', 'Timecode'),
    'meta.lens': ('Lente', 'Lens'),
    'meta.resolution': ('Resolução', 'Resolution'),
    'meta.codec': ('Codec', 'Codec'),
    'meta.xavc': ('Formato', 'Format'),
    'meta.fps': ('Quadros/s', 'Frame rate'),
    'meta.capture': ('captura', 'capture'),
    'meta.duration': ('Duração', 'Duration'),
    'meta.bitrate': ('Bitrate do vídeo', 'Video bitrate'),
    'meta.bitrate_file': ('Bitrate (arquivo)', 'Bitrate (file)'),
    'meta.color': ('Cor', 'Color'),
    'meta.audio': ('Áudio', 'Audio'),
    'meta.size': ('Tamanho', 'Size'),
    'meta.none': ('Selecione um clipe para ver os detalhes.', 'Select a clip to see its details.'),
    'meta.copy': ('Copiar', 'Copy'),
    'meta.preview': ('Prévia (10 % · 50 % · 90 %)', 'Preview (10% · 50% · 90%)'),
    'meta.preview_loading': ('Gerando prévia…', 'Generating preview…'),
    'meta.preview_after': ('A prévia aparece depois do reparo.', 'The preview appears after the repair.'),
    'meta.preview_noff': ('Prévia indisponível: ffmpeg não encontrado.', 'Preview unavailable: ffmpeg not found.'),
    'meta.preview_fail': ('Não foi possível gerar a prévia deste clipe.', 'Could not generate a preview for this clip.'),
    'meta.preview_log': ('Imagem em S-Log3, sem LUT.', 'S-Log3 image, no LUT applied.'),

    # ---- CLI
    'cli.desc': ('Diagnóstico e recuperação de clipes Sony XAVC S corrompidos.',
                 'Diagnose and recover corrupted Sony XAVC S clips.'),
    'cli.scan': ('analisa uma pasta ou arquivos e lista os problemas', 'analyze a folder or files and list problems'),
    'cli.repair': ('analisa e repara (gera cópias; originais intactos)',
                   'analyze and repair (writes copies; originals untouched)'),
    'cli.targets': ('uma pasta, ou um ou mais arquivos', 'a folder, or one or more files'),
    'cli.csv': ('salva o diagnóstico em CSV', 'save the diagnosis as CSV'),
    'cli.output': ('pasta de saída', 'output folder'),
    'cli.beside': ('grava cada recuperado em <pasta do clipe>/Second Take (em vez de -o)',
                   "write each recovered file to <clip folder>/Second Take (instead of -o)"),
    'cli.out_inside': ('aviso: a pasta de saída está dentro da pasta de origem ({path})',
                       'warning: the output folder is inside the source folder ({path})'),
    'cli.out_same': ('saída = pasta de origem: os recuperados vão para a subpasta "Second Take"',
                     'output = source folder: recovered files go to the "Second Take" subfolder'),
    'cli.verify': ('confere os reparados com ffprobe', 'check repaired files with ffprobe'),
    'cli.mode': ('força um modo de reparo (padrão: automático)', 'force a repair mode (default: automatic)'),
    'cli.ref': ('clipe de referência manual', 'manual reference clip'),
    'cli.data_start': ('offset manual do início dos dados (bytes)', 'manual data start offset (bytes)'),
    'cli.lang': ('idioma das mensagens (pt ou en)', 'message language (pt or en)'),
    'cli.not_found': ('não encontrado: {paths}', 'not found: {paths}'),
    'cli.summary': ('{n} arquivos · {ok} OK · {bad} com problema', '{n} files · {ok} OK · {bad} with problems'),
    'cli.report': ('relatório: {path}', 'report: {path}'),
    'cli.skip': ('pulando {name}: sem referência compatível', 'skipping {name}: no compatible reference'),
    'cli.repairing': ('reparando {name} ({mode}, ref. {ref})', 'repairing {name} ({mode}, ref. {ref})'),

    # ---- interface
    'app.tagline': ('Recuperação de clipes Sony XAVC S · {v}', 'Sony XAVC S clip recovery · {v}'),
    'app.scan_folder': ('Analisar pasta', 'Analyze folder'),
    'app.scan_files': ('Analisar arquivos', 'Analyze files'),
    'app.m_total': ('Analisados', 'Analyzed'),
    'app.m_ok': ('Íntegros', 'Intact'),
    'app.m_fix': ('Reparáveis', 'Repairable'),
    'app.m_bad': ('Último recurso', 'Last resort'),
    'app.view_bad': ('Problemas', 'Problems'),
    'app.view_all': ('Todos', 'All'),
    'app.search': ('Filtrar por nome', 'Filter by name'),
    'app.col_clip': ('Clipe', 'Clip'),
    'app.col_status': ('Status', 'Status'),
    'app.col_action': ('Ação', 'Action'),
    'app.col_audio': ('Áudio', 'Audio'),
    'app.col_size': ('Tamanho', 'Size'),
    'app.col_ref': ('Referência', 'Reference'),
    'app.col_detail': ('Diagnóstico', 'Diagnosis'),
    'app.audio_silent': ('mudo', 'silent'),
    'app.empty_title': ('Comece pelos clipes com problema', 'Start with the problem clips'),
    'app.empty_body': ('Analise uma pasta de brutas inteira ou escolha só os arquivos que não abrem.\n'
                       'Os originais nunca são alterados: tudo sai como cópia na pasta de saída.',
                       'Analyze a whole camera-original folder, or pick just the files that won\'t open.\n'
                       'Originals are never modified: everything is written as a copy to the output folder.'),
    'app.drop_title': ('Arraste clipes ou uma pasta aqui', 'Drop clips or a folder here'),
    'app.drop_none': ('Nenhum clipe MP4/MOV no que foi solto.', 'No MP4/MOV clips in what was dropped.'),
    'app.output': ('Saída', 'Output'),
    'app.change': ('Alterar', 'Change'),
    'app.verify_switch': ('Verificar com ffprobe', 'Verify with ffprobe'),
    'app.cancel': ('Cancelar', 'Cancel'),
    'app.cancelling': ('Cancelando… o clipe em andamento será descartado',
                       'Cancelling… the clip in progress will be discarded'),
    'app.cancelled': ('Cancelado: {n} de {total} clipe(s) processado(s) · {report} gravado em {path}',
                      'Cancelled: {n} of {total} clip(s) processed · {report} written to {path}'),
    'app.beside_switch': ('Salvar junto dos originais', 'Save next to originals'),
    'app.out_beside': ('Pasta de cada clipe › Second Take', "Each clip's folder › Second Take"),
    'app.ready': ('Pronto.', 'Ready.'),
    'app.open_out': ('Abrir saída', 'Open output'),
    'app.export': ('Exportar CSV', 'Export CSV'),
    'app.advanced': ('Avançado…', 'Advanced…'),
    'app.repair': ('Reparar', 'Repair'),
    'app.repair_n': ('Reparar {n} clipe', 'Repair {n} clip'),
    'app.repair_n_pl': ('Reparar {n} clipes', 'Repair {n} clips'),
    'app.pick_out': ('Pasta de saída', 'Output folder'),
    'app.pick_src': ('Pasta de brutas', 'Camera-original folder'),
    'app.pick_files': ('Escolha os clipes com problema', 'Choose the problem clips'),
    'app.video': ('Vídeo', 'Video'),
    'app.scanning_folder': ('Analisando a pasta…', 'Analyzing folder…'),
    'app.scanning_files': ('Analisando os arquivos…', 'Analyzing files…'),
    'app.files_in': ('{n} arquivo(s) em {path}', '{n} file(s) in {path}'),
    'app.out_inside': ('A pasta de saída está dentro da origem. Continuar?',
                       'The output folder is inside the source. Continue?'),
    'app.cant_write': ('Não dá para gravar em {path}:\n{err}', 'Cannot write to {path}:\n{err}'),
    'app.no_space': ('Espaço insuficiente na saída: precisa de {need}, há {free} livres.',
                     'Not enough space in the output: {need} needed, {free} free.'),
    'app.confirm': ('Reparar {n} clipe(s)?\n\n{size} serão gravados em:\n{path}',
                    'Repair {n} clip(s)?\n\n{size} will be written to:\n{path}'),
    'app.intact': ('{name} está íntegro — nada a ajustar.', '{name} is intact — nothing to adjust.'),
    'app.saved': ('Relatório salvo em {path}', 'Report saved to {path}'),
    'app.analyzing': ('Analisando {i}/{n} · {name}', 'Analyzing {i}/{n} · {name}'),
    'app.done_scan': ('{n} clipe(s) analisado(s). ', '{n} clip(s) analyzed. '),
    'app.ready_n': ('{n} pronto(s) para reparar.', '{n} ready to repair.'),
    'app.nothing': ('Nada para reparar automaticamente.', 'Nothing to repair automatically.'),
    'app.extract_n': (' {n} só por extração bruta.', ' {n} only by raw extraction.'),
    'app.repairing': ('Reparando {name}…', 'Repairing {name}…'),
    'app.verifying': ('Verificando {name}…', 'Verifying {name}…'),
    'app.done_fix': ('{n} clipe(s) processado(s) · {report} gravado em {path}',
                     '{n} clip(s) processed · {report} written to {path}'),
    'app.scan_error': ('Erro na análise.', 'Analysis failed.'),
    'app.language': ('Idioma', 'Language'),
    'prefs.title': ('Ajustes', 'Settings'),
    'prefs.auto': ('Automático (idioma do sistema)', 'Automatic (system language)'),
    'prefs.updates': ('Atualizações', 'Updates'),
    'prefs.auto_update': ('Verificar atualizações automaticamente', 'Check for updates automatically'),
    'upd.menu': ('Verificar atualizações…', 'Check for Updates…'),
    'upd.available': ('Nova versão {v} disponível', 'Version {v} is available'),
    'upd.current': ('— você está na {v}.', '— you have {v}.'),
    'upd.download': ('Baixar', 'Download'),
    'upd.later': ('Agora não', 'Not now'),
    'upd.skip': ('Pular esta versão', 'Skip this version'),
    'upd.notes': ('Novidades', "What's new"),
    'upd.up_to_date': ('Você está usando a versão mais recente ({v}).', "You're on the latest version ({v})."),
    'upd.failed': ('Não foi possível verificar atualizações agora.\n{err}',
                   "Couldn't check for updates right now.\n{err}"),
    'upd.checking': ('Verificando atualizações…', 'Checking for updates…'),
    'prefs.lang_hint': ('No modo automático, o Second Take segue o idioma do sistema. Escolhendo um idioma, ele fica fixo.',
                        'In automatic mode, Second Take follows the system language. Picking a language locks it.'),

    # ---- Sobre
    'about.title': ('Sobre o Second Take', 'About Second Take'),
    'about.version': ('Versão {v}', 'Version {v}'),
    'about.tagline': ('Recuperação de clipes de câmera corrompidos.\nDá uma segunda chance ao take.',
                      'Recovery for corrupted camera clips.\nGives the take a second chance.'),
    'about.formats': ('Formatos', 'Formats'),
    'about.formats_v': ('Sony XAVC S (H.264, LPCM) — FX3, FX3A, FX30', 'Sony XAVC S (H.264, LPCM) — FX3, FX3A, FX30'),
    'about.recovers': ('Recupera', 'Recovers'),
    'about.recovers_v': ('cabeçalho, índice (moov), trechos danificados, extração bruta',
                         'header, index (moov), damaged sections, raw extraction'),
    'about.originals': ('Originais', 'Originals'),
    'about.originals_v': ('nunca são alterados — tudo sai como cópia', 'never modified — everything is a copy'),
    'about.ffprobe_missing': ('não encontrado — brew install ffmpeg', 'not found — brew install ffmpeg'),
    'about.system': ('Sistema', 'System'),
    'about.by': ('Desenvolvido por Felipe Lima · Super8 Produtora', 'Made by Felipe Lima · Super8 Produtora'),
    'about.legal': ('Copyright © 2026 Super8 Produtora · GPL v3 ou posterior\n'
                    'Distribuído sem garantia — mantenha sempre os originais.',
                    'Copyright © 2026 Super8 Produtora · GPL v3 or later\n'
                    'Provided without warranty — always keep the originals.'),
    'about.github': ('Projeto no GitHub', 'GitHub project'),
    'about.issue': ('Relatar problema', 'Report a problem'),
    'about.license': ('Licença', 'License'),
    'about.install': ('Como instalar em outro Mac', 'How to install on another Mac'),
    'about.install_win': ('Como instalar em outro PC', 'How to install on another PC'),
    'menu.file': ('Arquivo', 'File'),
    'menu.prefs': ('Ajustes…', 'Settings…'),
    'menu.quit': ('Sair', 'Exit'),
    'about.copy': ('Copiar informações', 'Copy info'),
    'about.close': ('Fechar', 'Close'),
    'about.copied': ('Instruções copiadas. Cole na mensagem para o colega junto com o link do instalador.',
                     'Instructions copied. Paste them in your message along with the installer link.'),
    'about.help_page': ('Página do projeto', 'Project page'),
    'about.menu_help': ('Ajuda', 'Help'),
    'install.help': (
        'Second Take — recuperação de clipes de câmera corrompidos\n'
        'Baixe o instalador (.dmg): {url}\n\n'
        '1. Abra o .dmg e arraste o Second Take para Aplicativos.\n'
        '2. Abra o app. O Mac vai avisar que não conseguiu verificar o app: feche o aviso (não mova para o Lixo).\n'
        '3. Ajustes do Sistema > Privacidade e Segurança > role até o fim > Abrir Mesmo Assim.\n'
        'Pronto, só precisa fazer isso uma vez.\n'
        '(Ou no Terminal: xattr -cr "/Applications/Second Take.app")',
        'Second Take — recovery for corrupted camera clips\n'
        'Download the installer (.dmg): {url}\n\n'
        '1. Open the .dmg and drag Second Take to Applications.\n'
        '2. Open the app. macOS will say it could not verify the app: close the warning (don\'t move it to the Trash).\n'
        '3. System Settings > Privacy & Security > scroll to the bottom > Open Anyway.\n'
        'That\'s it, you only need to do this once.\n'
        '(Or in Terminal: xattr -cr "/Applications/Second Take.app")'),

    'install.help_win': (
        'Second Take — recuperação de clipes de câmera corrompidos\n'
        'Baixe o instalador (SecondTake-…-Windows-x64-Setup.exe): {url}\n\n'
        '1. Abra o instalador. O Windows pode mostrar "O Windows protegeu o computador":\n'
        '   clique em "Mais informações" e depois em "Executar assim mesmo".\n'
        '2. Siga o instalador (não precisa de senha de administrador).\n'
        '3. Abra o Second Take pelo menu Iniciar.\n'
        '(Prefere sem instalar? Baixe o .zip portátil, descompacte e abra "Second Take.exe".)',
        'Second Take — recovery for corrupted camera clips\n'
        'Download the installer (SecondTake-…-Windows-x64-Setup.exe): {url}\n\n'
        '1. Open the installer. Windows may show "Windows protected your PC":\n'
        '   click "More info" and then "Run anyway".\n'
        '2. Follow the installer (no administrator password needed).\n'
        '3. Open Second Take from the Start menu.\n'
        '(Rather not install? Download the portable .zip, unzip it and open "Second Take.exe".)'),

    # ---- Avançado
    'adv.title': ('Avançado — {name}', 'Advanced — {name}'),
    'adv.mode': ('Modo', 'Mode'),
    'adv.m_header': ('Trocar cabeçalho (índice íntegro)', 'Replace header (intact index)'),
    'adv.m_rebuild': ('Reconstruir índice', 'Rebuild index'),
    'adv.m_extract': ('Extração bruta (.h264 / .wav)', 'Raw extraction (.h264 / .wav)'),
    'adv.ref': ('Referência', 'Reference'),
    'adv.other': ('Outro…', 'Other…'),
    'adv.offset': ('Início dos dados', 'Data start'),
    'adv.offset_ph': ('automático', 'automatic'),
    'adv.offset_hint': ('bytes · vazio = automático (262144 nas Sony XAVC S)',
                        'bytes · empty = automatic (262144 on Sony XAVC S)'),
    'adv.simulate': ('Simular', 'Simulate'),
    'adv.apply': ('Aplicar', 'Apply'),
    'adv.pick_ref': ('Clipe de referência', 'Reference clip'),
    'adv.need_ref': ('Escolha um clipe de referência válido.', 'Choose a valid reference clip.'),
    'adv.need_int': ('O início dos dados precisa ser um número inteiro de bytes.',
                     'The data start must be a whole number of bytes.'),
    'adv.ref_line': ('Referência: {name}', 'Reference: {name}'),
    'adv.ref_line_x': ('Referência (SPS/PPS e fps): {name}', 'Reference (SPS/PPS and fps): {name}'),
    'adv.failed': ('Não foi possível com essa configuração:\n{err}', 'Not possible with these settings:\n{err}'),
}


# ---------------------------------------------------------------- mensagens

def _render(v):
    return str(v) if isinstance(v, L) else v


class L:
    """Mensagem traduzível "preguiçosa": guarda a chave e os parâmetros e só traduz em str()."""
    __slots__ = ('key', 'kw')

    def __init__(self, key: str, **kw):
        self.key = key
        self.kw = kw

    def __str__(self):
        return t(self.key, **self.kw)

    def __repr__(self):
        return f'L({self.key!r})'

    def __eq__(self, other):
        if isinstance(other, L):
            return self.key == other.key and self.kw == other.kw
        return str(self) == other

    def __hash__(self):
        return hash(self.key)

    def __contains__(self, item):
        return item in str(self)

    def __format__(self, spec):
        return format(str(self), spec)


def t(key: str, **kw) -> str:
    pair = CATALOG.get(key)
    if pair is None:
        return key
    text = pair[1] if _lang == 'en' else pair[0]
    if kw:
        text = text.format(**{k: _render(v) for k, v in kw.items()})
    return text


def msg(e: BaseException):
    """Mensagem de uma exceção, preservando L quando houver."""
    if e.args and isinstance(e.args[0], (L, Join)):
        return e.args[0]
    return str(e)


class Join:
    """Sequência de partes (L ou str) unidas na hora de exibir."""
    __slots__ = ('parts', 'sep')

    def __init__(self, *parts, sep=''):
        self.parts = [p for p in parts if p not in (None, '')]
        self.sep = sep

    def __str__(self):
        return self.sep.join(str(p) for p in self.parts)

    def __eq__(self, other):
        return str(self) == str(other)

    def __hash__(self):
        return hash(str(self))

    def __contains__(self, item):
        return item in str(self)

    def __format__(self, spec):
        return format(str(self), spec)

    def __bool__(self):
        return bool(self.parts)


# ---------------------------------------------------------------- idioma

def get_lang() -> str:
    return _lang


def set_lang(lang: str):
    global _lang
    _lang = lang if lang in LANGS else 'pt'


def _config_path() -> str:
    if sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support/Second Take')
    elif sys.platform == 'win32':
        base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'Second Take')
    else:
        base = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'secondtake')
    return os.path.join(base, 'config.json')


_CONFIG_LOCK = threading.RLock()     # a thread do updater e a interface gravam o mesmo arquivo


def load_config() -> dict:
    with _CONFIG_LOCK:
        try:
            with open(_config_path(), encoding='utf-8') as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except (OSError, ValueError):
            return {}


def save_config(**values):
    """Ler-mesclar-gravar sob trava, gravando num .tmp e trocando com os.replace:
    um app fechado no meio da gravação nunca deixa o config.json pela metade."""
    with _CONFIG_LOCK:
        cfg = load_config()
        cfg.update(values)
        path = _config_path()
        tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass


def system_lang() -> str:
    if sys.platform == 'darwin':
        try:
            out = subprocess.run(['defaults', 'read', '-g', 'AppleLanguages'], capture_output=True,
                                 text=True, timeout=2).stdout
            first = next((x.strip(' "\n,') for x in out.splitlines() if x.strip(' "\n,()')), '')
            if first:
                return 'pt' if first.lower().startswith('pt') else 'en'
        except Exception:
            pass
    if sys.platform == 'win32':
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return 'pt' if (langid & 0x3FF) == 0x16 else 'en'   # 0x16 = LANG_PORTUGUESE
        except Exception:
            pass
    for v in (os.environ.get('LC_ALL'), os.environ.get('LANG'), (locale.getlocale()[0] or '')):
        if v:
            return 'pt' if v.lower().startswith(('pt', 'portuguese')) else 'en'
    return 'pt'


def preference() -> str:
    """Preferência salva: 'auto' (segue o sistema), 'pt' ou 'en'."""
    p = load_config().get('lang', 'auto')
    return p if p in LANGS else 'auto'


def set_preference(pref: str) -> str:
    """Salva 'auto', 'pt' ou 'en' e aplica. Retorna o idioma efetivo."""
    pref = pref if pref in LANGS else 'auto'
    save_config(lang=pref)
    set_lang(system_lang() if pref == 'auto' else pref)
    return _lang


def init(explicit: str | None = None) -> str:
    """Define o idioma: argumento > SECONDTAKE_LANG > preferência salva > sistema."""
    pref = preference()
    lang = explicit or os.environ.get('SECONDTAKE_LANG') or (None if pref == 'auto' else pref) or system_lang()
    set_lang(lang)
    return _lang
