#!/usr/bin/env python3
"""Second Take — interface gráfica (CustomTkinter).

A lógica de diagnóstico e reparo fica toda em `core`/`rebuild`; este módulo só
cuida da apresentação. O tema acompanha o modo claro/escuro do sistema.
"""
from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from . import __version__
from . import core

REPO_URL = 'https://github.com/felipelimas8/secondtake'
ISSUES_URL = REPO_URL + '/issues'
LICENSE_URL = 'https://www.gnu.org/licenses/gpl-3.0.html'

FONT = 'SF Pro Text' if sys.platform == 'darwin' else 'Segoe UI' if sys.platform == 'win32' else 'Helvetica'
MONO = 'SF Mono' if sys.platform == 'darwin' else 'Consolas' if sys.platform == 'win32' else 'Courier'

# paleta (claro, escuro) — mesma lógica de cores do mockup
PAL = {
    'bg':       ('#F5F5F3', '#1C1C1C'),
    'card':     ('#FFFFFF', '#262626'),
    'card2':    ('#EFEFEC', '#2E2E2E'),
    'border':   ('#E2E2DE', '#3A3A3A'),
    'text':     ('#1F1F1D', '#ECECEA'),
    'muted':    ('#6E6E69', '#A3A39E'),
    'select':   ('#DCE8FA', '#1E3A5F'),
    'accent':   ('#1F6FEB', '#4C8DF6'),
    'accent_h': ('#1A5FCC', '#3B7BE6'),
    'ok':       ('#2E7D32', '#7FD18B'),
    'warn':     ('#B26A00', '#F2B85B'),
    'info':     ('#1565C0', '#7DB4F5'),
    'danger':   ('#C62828', '#F28B82'),
}

STATUS_LABEL = {
    core.OK: 'Íntegro',
    core.HEADER: 'Cabeçalho',
    core.NO_MOOV: 'Sem índice',
    core.EMPTY: 'Vazio',
    core.UNKNOWN: 'Desconhecido',
}
STATUS_ICON = {'ok': '✓', 'warn': '●', 'info': '●', 'danger': '▲', 'muted': '·'}
MODE_LABEL = {'cabecalho': 'Reparar', 'reconstruir': 'Reconstruir', 'extrair': 'Extrair'}


def human(n):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {u}' if u != 'B' else f'{n} B'
        n /= 1024
    return f'{n:.1f} PB'


def _mode_idx():
    return 1 if ctk.get_appearance_mode() == 'Dark' else 0


def col(key):
    return PAL[key][_mode_idx()]


def kind(c: core.Clip) -> str:
    """Categoria visual do clipe: ok, warn, info, danger ou muted."""
    if c.status == core.OK:
        return 'ok'
    if c.status in core.REPAIRABLE and c.reference:
        mode = c.extra.get('mode')
        if mode == 'reconstruir':
            return 'info'
        if mode == 'extrair':
            return 'danger'
        return 'warn'
    return 'danger'


# ======================================================================= app

class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode('system')
        ctk.set_default_color_theme('blue')
        super().__init__(fg_color=PAL['bg'])
        self.title(f'Second Take {__version__}')
        self.geometry('1240x760')
        self.minsize(980, 600)
        self.clips: list[core.Clip] = []
        self.q = queue.Queue()
        self.busy = False
        self.src = tk.StringVar()
        self.dst = tk.StringVar(value=os.path.expanduser('~/Movies/SecondTake'))
        self.view = tk.StringVar(value='Todos')
        self.do_verify = tk.BooleanVar(value=bool(core.ffmpeg_bin('ffprobe')))
        self._mode = None
        self._build()
        self._install_about()
        self._apply_tree_style()
        self._show_empty(True)
        self.after(100, self._poll)

    # ------------------------------------------------------------ layout
    def _build(self):
        f = (FONT, 13)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # cabeçalho
        head = ctk.CTkFrame(self, fg_color=PAL['card'], corner_radius=0, border_width=0)
        head.grid(row=0, column=0, sticky='ew')
        head.grid_columnconfigure(1, weight=1)
        logo = ctk.CTkLabel(head, text='T2', width=40, height=40, corner_radius=10,
                            fg_color=PAL['accent'], text_color='#FFFFFF', font=(FONT, 15, 'bold'))
        logo.grid(row=0, column=0, rowspan=2, padx=(20, 12), pady=14)
        ctk.CTkLabel(head, text='Second Take', font=(FONT, 18, 'bold'), text_color=PAL['text'],
                     anchor='w').grid(row=0, column=1, sticky='sw', pady=(14, 0))
        self.subtitle = ctk.CTkLabel(head, text=f'Recuperação de clipes Sony XAVC S · {__version__}',
                                     font=(FONT, 12), text_color=PAL['muted'], anchor='w')
        self.subtitle.grid(row=1, column=1, sticky='nw', pady=(0, 14))
        self.btn_scan = ctk.CTkButton(head, text='Analisar pasta', width=140, height=34, font=f,
                                      command=self.scan, fg_color='transparent', border_width=1,
                                      border_color=PAL['border'], text_color=PAL['text'],
                                      hover_color=PAL['card2'])
        self.btn_scan.grid(row=0, column=2, rowspan=2, padx=6)
        self.btn_files = ctk.CTkButton(head, text='Analisar arquivos', width=150, height=34, font=f,
                                       command=self.scan_selected, fg_color='transparent', border_width=1,
                                       border_color=PAL['border'], text_color=PAL['text'],
                                       hover_color=PAL['card2'])
        self.btn_files.grid(row=0, column=3, rowspan=2, padx=(6, 20))
        ctk.CTkFrame(self, height=1, fg_color=PAL['border'], corner_radius=0).grid(row=0, column=0, sticky='sew')

        # métricas
        met = ctk.CTkFrame(self, fg_color='transparent')
        met.grid(row=1, column=0, sticky='ew', padx=20, pady=(16, 8))
        self.metric = {}
        for i, (key, label, color) in enumerate((('total', 'Analisados', 'text'), ('ok', 'Íntegros', 'ok'),
                                                 ('fix', 'Reparáveis', 'warn'),
                                                 ('bad', 'Último recurso', 'danger'))):
            met.grid_columnconfigure(i, weight=1, uniform='m')
            card = ctk.CTkFrame(met, fg_color=PAL['card'], corner_radius=10, border_width=1,
                                border_color=PAL['border'])
            card.grid(row=0, column=i, sticky='ew', padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            ctk.CTkLabel(card, text=label, font=(FONT, 12), text_color=PAL['muted'],
                         anchor='w').pack(anchor='w', padx=14, pady=(10, 0))
            v = ctk.CTkLabel(card, text='—', font=(FONT, 24, 'bold'), text_color=PAL[color], anchor='w')
            v.pack(anchor='w', padx=14, pady=(0, 10))
            self.metric[key] = v

        # filtros
        flt = ctk.CTkFrame(self, fg_color='transparent')
        flt.grid(row=2, column=0, sticky='ew', padx=20, pady=(4, 8))
        flt.grid_columnconfigure(1, weight=1)
        self.seg = ctk.CTkSegmentedButton(flt, values=['Problemas', 'Todos'], variable=self.view,
                                          command=lambda _: self._refresh(), font=(FONT, 12), height=30)
        self.seg.grid(row=0, column=0, sticky='w')
        self.search = ctk.CTkEntry(flt, placeholder_text='Filtrar por nome', width=220, height=30,
                                   font=(FONT, 12))
        self.search.grid(row=0, column=2, sticky='e')
        self.search.bind('<KeyRelease>', lambda e: self._refresh())

        # tabela
        self.body = ctk.CTkFrame(self, fg_color=PAL['card'], corner_radius=10, border_width=1,
                                 border_color=PAL['border'])
        self.body.grid(row=3, column=0, sticky='nsew', padx=20)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        cols = ('status', 'action', 'audio', 'size', 'ref', 'detail')
        heads = ('Status', 'Ação', 'Áudio', 'Tamanho', 'Referência', 'Diagnóstico')
        widths = (130, 100, 80, 90, 190, 420)
        self.tree = ttk.Treeview(self.body, columns=cols, show='tree headings', selectmode='extended',
                                 style='ST.Treeview')
        self.tree.heading('#0', text='Clipe', anchor='w')
        self.tree.column('#0', width=230, stretch=False)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h, anchor='w')
            self.tree.column(c, width=w, stretch=c == 'detail', anchor='w')
        sb = ctk.CTkScrollbar(self.body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(2, 0), pady=2)
        sb.grid(row=0, column=1, sticky='ns', pady=6)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._update_actions())
        self.tree.bind('<Double-1>', lambda e: self.advanced())

        # estado vazio
        self.empty = ctk.CTkFrame(self.body, fg_color='transparent')
        ctk.CTkLabel(self.empty, text='Comece pelos clipes com problema', font=(FONT, 17, 'bold'),
                     text_color=PAL['text']).pack(pady=(0, 6))
        ctk.CTkLabel(self.empty, text='Analise uma pasta de brutas inteira ou escolha só os arquivos que não abrem.\n'
                                      'Os originais nunca são alterados: tudo sai como cópia na pasta de saída.',
                     font=(FONT, 13), text_color=PAL['muted'], justify='center').pack(pady=(0, 16))
        row = ctk.CTkFrame(self.empty, fg_color='transparent')
        row.pack()
        ctk.CTkButton(row, text='Analisar arquivos', height=36, font=f, command=self.scan_selected,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).pack(side='left', padx=6)
        ctk.CTkButton(row, text='Analisar pasta', height=36, font=f, command=self.scan,
                      fg_color='transparent', border_width=1, border_color=PAL['border'],
                      text_color=PAL['text'], hover_color=PAL['card2']).pack(side='left', padx=6)

        # rodapé
        foot = ctk.CTkFrame(self, fg_color=PAL['card'], corner_radius=0)
        foot.grid(row=4, column=0, sticky='ew', pady=(14, 0))
        ctk.CTkFrame(self, height=1, fg_color=PAL['border'], corner_radius=0).grid(row=4, column=0, sticky='new',
                                                                                   pady=(14, 0))
        foot.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(foot, text='Saída', font=(FONT, 12), text_color=PAL['muted']).grid(
            row=0, column=0, padx=(20, 8), pady=(12, 2), sticky='w')
        self.dst_lbl = ctk.CTkLabel(foot, textvariable=self.dst, font=(MONO, 12), text_color=PAL['text'],
                                    anchor='w', cursor='hand2')
        self.dst_lbl.grid(row=0, column=1, sticky='w', pady=(12, 2))
        self.dst_lbl.bind('<Button-1>', lambda e: self._pick_dst())
        ctk.CTkButton(foot, text='Alterar', width=70, height=26, font=(FONT, 12), command=self._pick_dst,
                      fg_color='transparent', text_color=PAL['accent'], hover_color=PAL['card2']).grid(
            row=0, column=2, sticky='w', pady=(12, 2))
        ctk.CTkSwitch(foot, text='Verificar com ffprobe', variable=self.do_verify, font=(FONT, 12),
                      text_color=PAL['text']).grid(row=0, column=3, padx=12, pady=(12, 2))

        self.status = ctk.CTkLabel(foot, text='Pronto.', font=(FONT, 12), text_color=PAL['muted'], anchor='w')
        self.status.grid(row=1, column=0, columnspan=4, sticky='ew', padx=20, pady=(0, 2))
        self.prog = ctk.CTkProgressBar(foot, height=6)
        self.prog.set(0)
        self.prog.grid(row=2, column=0, columnspan=4, sticky='ew', padx=20, pady=(0, 14))

        acts = ctk.CTkFrame(foot, fg_color='transparent')
        acts.grid(row=0, column=4, rowspan=3, padx=(8, 20), pady=12, sticky='e')
        sec = dict(fg_color='transparent', border_width=1, border_color=PAL['border'], text_color=PAL['text'],
                   hover_color=PAL['card2'], height=36, font=f)
        self.btn_open = ctk.CTkButton(acts, text='Abrir saída', width=110, command=self._open_out, **sec)
        self.btn_open.pack(side='left', padx=4)
        self.btn_report = ctk.CTkButton(acts, text='Exportar CSV', width=120, command=self.export, **sec)
        self.btn_report.pack(side='left', padx=4)
        self.btn_adv = ctk.CTkButton(acts, text='Avançado…', width=110, command=self.advanced, **sec)
        self.btn_adv.pack(side='left', padx=4)
        self.btn_fix = ctk.CTkButton(acts, text='Reparar', width=170, height=36, font=(FONT, 13, 'bold'),
                                     command=self.repair, fg_color=PAL['accent'], hover_color=PAL['accent_h'])
        self.btn_fix.pack(side='left', padx=(8, 0))
        self._update_actions()

    def _apply_tree_style(self):
        """Treeview é ttk: recolore conforme o modo claro/escuro."""
        self._mode = ctk.get_appearance_mode()
        st = ttk.Style(self)
        try:
            st.theme_use('clam')
        except tk.TclError:
            pass
        bg, fg, head, sel = col('card'), col('text'), col('card2'), col('select')
        st.configure('ST.Treeview', background=bg, fieldbackground=bg, foreground=fg, rowheight=30,
                     borderwidth=0, font=(FONT, 12))
        st.configure('ST.Treeview.Heading', background=head, foreground=col('muted'), relief='flat',
                     borderwidth=0, font=(FONT, 11), padding=(8, 6))
        st.map('ST.Treeview.Heading', background=[('active', head)])
        st.map('ST.Treeview', background=[('selected', sel)], foreground=[('selected', fg)])
        st.layout('ST.Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        # íntegros ficam discretos; o que precisa de atenção usa a cor de texto normal
        self.tree.tag_configure('ok', foreground=col('muted'))
        for k in ('warn', 'info', 'danger', 'muted'):
            self.tree.tag_configure(k, foreground=col('text'))

    def _show_empty(self, show: bool):
        if show:
            self.empty.place(relx=0.5, rely=0.45, anchor='center')
        else:
            self.empty.place_forget()

    # ------------------------------------------------------------ Sobre
    def _install_about(self):
        if sys.platform == 'darwin':
            self.createcommand('tkAboutDialog', self.about)
            self.createcommand('::tk::mac::ShowHelp', lambda: webbrowser.open(REPO_URL))
        else:
            bar = tk.Menu(self)
            ajuda = tk.Menu(bar, tearoff=0)
            ajuda.add_command(label='Sobre o Second Take', command=self.about)
            ajuda.add_command(label='Página do projeto', command=lambda: webbrowser.open(REPO_URL))
            bar.add_cascade(label='Ajuda', menu=ajuda)
            self.config(menu=bar)

    def about(self):
        AboutDialog(self)

    # ------------------------------------------------------------ helpers
    def _pick_dst(self):
        d = filedialog.askdirectory(title='Pasta de saída', initialdir=self.dst.get())
        if d:
            self.dst.set(d)

    def _open_out(self):
        d = self.dst.get()
        os.makedirs(d, exist_ok=True)
        subprocess.run(['open', d] if sys.platform == 'darwin' else ['xdg-open', d])

    def _selected(self):
        return [self.clips[int(i)] for i in self.tree.selection()]

    def _todo(self):
        return [c for c in self._selected() if c.status in core.REPAIRABLE and c.reference]

    @staticmethod
    def _auto(c):
        """Selecionado automaticamente: reparo de cabeçalho ou reconstrução (não a extração bruta)."""
        return c.status in core.REPAIRABLE and c.reference and c.extra.get('mode') != 'extrair'

    def _update_actions(self):
        n = len(self._todo()) if self.clips else 0
        busy = self.busy
        self.btn_fix.configure(text=f'Reparar {n} clipe' + ('s' if n != 1 else '') if n else 'Reparar',
                               state='normal' if n and not busy else 'disabled')
        self.btn_adv.configure(state='normal' if len(self._selected()) == 1 and not busy else 'disabled')
        self.btn_report.configure(state='normal' if self.clips and not busy else 'disabled')
        for b in (self.btn_scan, self.btn_files):
            b.configure(state='disabled' if busy else 'normal')

    def _set_busy(self, b):
        self.busy = b
        self._update_actions()

    def _row(self, c):
        mode = c.extra.get('mode')
        action = MODE_LABEL.get(mode, '—') if c.status in core.REPAIRABLE else '—'
        if c.audio_db is None:
            audio = '—'
        elif c.audio_db < -75:
            audio = 'mudo'
        else:
            audio = f'{c.audio_db:.0f} dB'
        diag = c.verify or c.detail or '—'
        if c.verify and c.detail and not c.verify.startswith('ERRO'):
            diag = f'{c.verify} · {c.detail}'
        return (f'{STATUS_ICON[kind(c)]}  ' + STATUS_LABEL.get(c.status, c.status), action, audio,
                human(c.size), os.path.basename(c.reference) or '—', diag)

    def _metrics(self):
        if not self.clips:
            for v in self.metric.values():
                v.configure(text='—')
            return
        ks = [kind(c) for c in self.clips]
        self.metric['total'].configure(text=f'{len(self.clips):,}'.replace(',', '.'))
        self.metric['ok'].configure(text=str(ks.count('ok')))
        self.metric['fix'].configure(text=str(ks.count('warn') + ks.count('info')))
        self.metric['bad'].configure(text=str(ks.count('danger')))

    def _refresh(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        q = self.search.get().strip().lower() if hasattr(self, 'search') else ''
        only_bad = self.view.get() == 'Problemas'
        base = self.src.get()
        for i, c in enumerate(self.clips):
            if only_bad and c.status == core.OK:
                continue
            if q and q not in c.name.lower():
                continue
            rel = os.path.relpath(c.path, base) if base and c.path.startswith(base) else c.name
            self.tree.insert('', 'end', iid=str(i), text=rel, values=self._row(c), tags=(kind(c),))
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        self._show_empty(not self.clips)
        self._metrics()
        self._update_actions()

    def _select_repairable(self):
        ids = [str(i) for i, c in enumerate(self.clips) if self._auto(c) and self.tree.exists(str(i))]
        self.tree.selection_set(ids)
        self._update_actions()

    # ------------------------------------------------------------ ações
    def _start_scan(self, label, fn):
        self.clips = []
        self._refresh()
        self._show_empty(False)
        self._set_busy(True)
        self.status.configure(text=label)

        def work():
            def prog(i, n, c):
                self.q.put(('scan', i, n, c.name))
            try:
                self.q.put(('scan_done', fn(prog)))
            except Exception as e:
                self.q.put(('error', str(e)))
        threading.Thread(target=work, daemon=True).start()

    def scan(self):
        d = filedialog.askdirectory(title='Pasta de brutas', initialdir=self.src.get() or None)
        if not d:
            return
        self.src.set(d)
        self.subtitle.configure(text=d)
        self._start_scan('Analisando a pasta…', lambda prog: core.scan(d, prog))

    def scan_selected(self):
        paths = filedialog.askopenfilenames(title='Escolha os clipes com problema',
                                            filetypes=[('Vídeo', '*.MP4 *.mp4 *.MOV *.mov')])
        if not paths:
            return
        base = os.path.commonpath([os.path.dirname(p) for p in paths])
        self.src.set(base)
        self.subtitle.configure(text=f'{len(paths)} arquivo(s) em {base}')
        self._start_scan('Analisando os arquivos…', lambda prog: core.scan_files(list(paths), prog))

    def repair(self):
        todo = self._todo()
        if not todo:
            return
        out = self.dst.get()
        if self.src.get() and os.path.abspath(out).startswith(os.path.abspath(self.src.get()) + os.sep):
            if not messagebox.askyesno('Second Take', 'A pasta de saída está dentro da origem. Continuar?'):
                return
        total = sum(c.size for c in todo)
        try:
            os.makedirs(out, exist_ok=True)
            st_ = os.statvfs(out) if hasattr(os, 'statvfs') else None
            free = st_.f_bavail * st_.f_frsize if st_ else float('inf')
        except OSError as e:
            messagebox.showerror('Second Take', f'Não dá para gravar em {out}:\n{e}')
            return
        if free < total * 1.05:
            messagebox.showerror('Second Take', f'Espaço insuficiente na saída: precisa de {human(total)}, '
                                                f'há {human(free)} livres.')
            return
        if not messagebox.askyesno('Second Take', f'Reparar {len(todo)} clipe(s)?\n\n'
                                                  f'{human(total)} serão gravados em:\n{out}'):
            return
        self._set_busy(True)
        verify = self.do_verify.get()

        def work():
            done = 0
            for c in todo:
                try:
                    self.q.put(('fix_start', c.name))
                    core.repair(c, out, lambda b, n, base=done: self.q.put(('fix', base + b, total)))
                    if verify:
                        self.q.put(('verify', c.name))
                        core.verify(c)
                except Exception as e:
                    c.verify = f'ERRO: {e}'
                done += c.size
                self.q.put(('fix_one', c))
            try:
                core.write_report(self.clips, os.path.join(out, 'relatorio_reparo.csv'))
            except Exception:
                pass
            self.q.put(('fix_done', len(todo), out))
        threading.Thread(target=work, daemon=True).start()

    def advanced(self):
        sel = self._selected()
        if len(sel) != 1:
            return
        c = sel[0]
        if c.status not in core.REPAIRABLE:
            messagebox.showinfo('Second Take', f'{c.name} está íntegro — nada a ajustar.')
            return
        AdvancedDialog(self, c)

    def export(self):
        p = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='diagnostico_secondtake.csv')
        if p:
            core.write_report(self.clips, p)
            self.status.configure(text=f'Relatório salvo em {p}')

    # ------------------------------------------------------------ fila
    def _poll(self):
        if ctk.get_appearance_mode() != self._mode:
            self._apply_tree_style()
        try:
            while True:
                msg = self.q.get_nowait()
                k = msg[0]
                if k == 'scan':
                    _, i, n, name = msg
                    self.prog.set(i / max(n, 1))
                    self.status.configure(text=f'Analisando {i}/{n} · {name}')
                elif k == 'scan_done':
                    self.clips = msg[1]
                    bad = any(c.status != core.OK for c in self.clips)
                    self.view.set('Problemas' if bad and len(self.clips) > 20 else 'Todos')
                    self._refresh()
                    self._select_repairable()
                    n_fix = len(self._todo())
                    n_ext = sum(1 for c in self.clips if kind(c) == 'danger')
                    msg_ = f'{len(self.clips)} clipe(s) analisado(s). '
                    msg_ += f'{n_fix} pronto(s) para reparar.' if n_fix else 'Nada para reparar automaticamente.'
                    if n_ext:
                        msg_ += f' {n_ext} só por extração bruta.'
                    self.status.configure(text=msg_)
                    self.prog.set(1)
                    self._set_busy(False)
                elif k == 'fix_start':
                    self.status.configure(text=f'Reparando {msg[1]}…')
                elif k == 'fix':
                    self.prog.set(msg[1] / max(msg[2], 1))
                elif k == 'verify':
                    self.status.configure(text=f'Verificando {msg[1]}…')
                elif k == 'fix_one':
                    c = msg[1]
                    i = str(self.clips.index(c))
                    if self.tree.exists(i):
                        self.tree.item(i, values=self._row(c), tags=(kind(c),))
                elif k == 'fix_done':
                    self.status.configure(text=f'{msg[1]} clipe(s) processado(s) · relatorio_reparo.csv '
                                               f'gravado em {msg[2]}')
                    self.prog.set(1)
                    self._set_busy(False)
                elif k == 'error':
                    messagebox.showerror('Second Take', msg[1])
                    self.status.configure(text='Erro na análise.')
                    self._set_busy(False)
                    self._refresh()
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ======================================================================= diálogos

class _Dialog(ctk.CTkToplevel):
    def __init__(self, app, title, size):
        super().__init__(app, fg_color=PAL['card'])
        self.app = app
        self.title(title)
        self.geometry(size)
        self.resizable(False, False)
        self.transient(app)
        self.bind('<Escape>', lambda e: self.destroy())
        self.after(50, self._center)
        self.after(80, self.lift)

    def _center(self):
        self.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - self.winfo_width()) // 2
        y = self.app.winfo_rooty() + 80
        self.geometry(f'+{max(x, 0)}+{max(y, 0)}')


class AboutDialog(_Dialog):
    """Janela Sobre: versão, o que faz, autoria, licença e ambiente."""

    def __init__(self, app):
        super().__init__(app, 'Sobre o Second Take', '540x700')
        f = ctk.CTkFrame(self, fg_color='transparent')
        f.pack(fill='both', expand=True, padx=28, pady=22)
        ctk.CTkLabel(f, text='T2', width=64, height=64, corner_radius=16, fg_color=PAL['accent'],
                     text_color='#FFFFFF', font=(FONT, 24, 'bold')).pack(pady=(0, 10))
        ctk.CTkLabel(f, text='Second Take', font=(FONT, 22, 'bold'), text_color=PAL['text']).pack()
        ctk.CTkLabel(f, text=f'Versão {__version__}', font=(FONT, 12), text_color=PAL['muted']).pack(pady=(0, 8))
        ctk.CTkLabel(f, text='Recuperação de clipes de câmera corrompidos.\nDá uma segunda chance ao take.',
                     font=(FONT, 13), text_color=PAL['text'], justify='center').pack()

        ff = core.ffmpeg_bin('ffprobe')
        self._rows = [
            ('Formatos', 'Sony XAVC S (H.264, LPCM) — FX3, FX3A, FX30'),
            ('Recupera', 'cabeçalho, índice (moov), trechos danificados, extração bruta'),
            ('Originais', 'nunca são alterados — tudo sai como cópia'),
            ('ffprobe', ff if ff else 'não encontrado — brew install ffmpeg'),
            ('Sistema', f'macOS {platform.mac_ver()[0]} · {platform.machine()}' if sys.platform == 'darwin'
             else f'{platform.system()} {platform.release()}'),
            ('Python', f'{platform.python_version()} · Tk {self.tk.call("info", "patchlevel")} · '
                       f'CustomTkinter {ctk.__version__}'),
        ]
        box = ctk.CTkFrame(f, fg_color=PAL['card2'], corner_radius=10)
        box.pack(fill='x', pady=16)
        box.grid_columnconfigure(1, weight=1)
        for i, (k, v) in enumerate(self._rows):
            ctk.CTkLabel(box, text=k, font=(FONT, 12), text_color=PAL['muted'], anchor='nw').grid(
                row=i, column=0, sticky='nw', padx=(14, 10), pady=(10 if i == 0 else 2, 10 if i == 5 else 2))
            ctk.CTkLabel(box, text=v, font=(FONT, 12), text_color=PAL['text'], anchor='w', justify='left',
                         wraplength=320).grid(row=i, column=1, sticky='w', padx=(0, 14),
                                              pady=(10 if i == 0 else 2, 10 if i == 5 else 2))

        ctk.CTkLabel(f, text='Desenvolvido por Felipe Lima · Super8 Produtora', font=(FONT, 12),
                     text_color=PAL['text']).pack()
        ctk.CTkLabel(f, text='Copyright © 2026 Super8 Produtora · GPL v3 ou posterior\n'
                             'Distribuído sem garantia — mantenha sempre os originais.',
                     font=(FONT, 11), text_color=PAL['muted'], justify='center').pack(pady=(2, 10))
        links = ctk.CTkFrame(f, fg_color='transparent')
        links.pack()
        for text, url in (('Projeto no GitHub', REPO_URL), ('Relatar problema', ISSUES_URL),
                          ('Licença', LICENSE_URL)):
            ctk.CTkButton(links, text=text, width=10, height=26, font=(FONT, 12), fg_color='transparent',
                          text_color=PAL['accent'], hover_color=PAL['card2'],
                          command=lambda u=url: webbrowser.open(u)).pack(side='left', padx=4)
        b = ctk.CTkFrame(f, fg_color='transparent')
        b.pack(pady=(14, 0))
        ctk.CTkButton(b, text='Copiar informações', height=32, font=(FONT, 12), command=self._copy,
                      fg_color='transparent', border_width=1, border_color=PAL['border'],
                      text_color=PAL['text'], hover_color=PAL['card2']).pack(side='left', padx=4)
        ctk.CTkButton(b, text='Fechar', height=32, width=90, font=(FONT, 12), command=self.destroy,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).pack(side='left', padx=4)

    def _copy(self):
        txt = f'Second Take {__version__}\n' + '\n'.join(f'{k}: {v}' for k, v in self._rows)
        self.clipboard_clear()
        self.clipboard_append(txt)


class AdvancedDialog(_Dialog):
    """Controles manuais: modo, referência, offset dos dados e simulação."""

    def __init__(self, app: App, clip):
        super().__init__(app, f'Avançado — {clip.name}', '640x500')
        self.c = clip
        refs = [r for r in app.clips if r.status == core.OK]
        base = app.src.get()
        names = [os.path.relpath(r.path, base) if base else r.path for r in refs]
        cur = os.path.relpath(clip.reference, base) if clip.reference and base else (clip.reference or '')
        self.ref = tk.StringVar(value=cur)
        self.offset = tk.StringVar(value='')
        self.mode = tk.StringVar(value=clip.extra.get('mode') or
                                 ('cabecalho' if clip.status == core.HEADER else 'reconstruir'))
        f = ctk.CTkFrame(self, fg_color='transparent')
        f.pack(fill='both', expand=True, padx=24, pady=20)
        f.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(f, text=clip.name, font=(MONO, 14, 'bold'), text_color=PAL['text'], anchor='w').grid(
            row=0, column=0, columnspan=3, sticky='w')
        ctk.CTkLabel(f, text=STATUS_LABEL.get(clip.status, clip.status), font=(FONT, 12),
                     text_color=PAL[kind(clip)], anchor='w').grid(row=1, column=0, columnspan=3, sticky='w',
                                                                  pady=(0, 14))

        ctk.CTkLabel(f, text='Modo', font=(FONT, 12), text_color=PAL['muted']).grid(row=2, column=0, sticky='nw',
                                                                                  padx=(0, 12))
        mf = ctk.CTkFrame(f, fg_color='transparent')
        mf.grid(row=2, column=1, columnspan=2, sticky='w')
        for val, txt, enabled in (('cabecalho', 'Trocar cabeçalho (índice íntegro)', clip.status == core.HEADER),
                                  ('reconstruir', 'Reconstruir índice', True),
                                  ('extrair', 'Extração bruta (.h264 / .wav)', True)):
            ctk.CTkRadioButton(mf, text=txt, value=val, variable=self.mode, font=(FONT, 12),
                               text_color=PAL['text'],
                               state='normal' if enabled else 'disabled').pack(anchor='w', pady=2)

        ctk.CTkLabel(f, text='Referência', font=(FONT, 12), text_color=PAL['muted']).grid(
            row=3, column=0, sticky='w', padx=(0, 12), pady=(14, 0))
        ctk.CTkComboBox(f, variable=self.ref, values=names[:300] or [''], font=(FONT, 12),
                        dropdown_font=(FONT, 12), height=30).grid(row=3, column=1, sticky='ew', pady=(14, 0))
        ctk.CTkButton(f, text='Outro…', width=70, height=30, font=(FONT, 12), command=self._browse,
                      fg_color='transparent', border_width=1, border_color=PAL['border'],
                      text_color=PAL['text'], hover_color=PAL['card2']).grid(row=3, column=2, padx=(8, 0),
                                                                             pady=(14, 0))

        ctk.CTkLabel(f, text='Início dos dados', font=(FONT, 12), text_color=PAL['muted']).grid(
            row=4, column=0, sticky='w', padx=(0, 12), pady=(10, 0))
        ctk.CTkEntry(f, textvariable=self.offset, width=140, height=30, font=(MONO, 12),
                     placeholder_text='automático').grid(row=4, column=1, sticky='w', pady=(10, 0))
        ctk.CTkLabel(f, text='bytes · vazio = automático (262144 nas Sony XAVC S)', font=(FONT, 11),
                     text_color=PAL['muted']).grid(row=5, column=1, columnspan=2, sticky='w')

        self.result = ctk.CTkTextbox(f, height=110, font=(FONT, 12), fg_color=PAL['card2'],
                                     text_color=PAL['text'], wrap='word', corner_radius=8)
        self.result.grid(row=6, column=0, columnspan=3, sticky='nsew', pady=14)
        self.result.insert('end', clip.detail or '')
        f.grid_rowconfigure(6, weight=1)

        b = ctk.CTkFrame(f, fg_color='transparent')
        b.grid(row=7, column=0, columnspan=3, sticky='e')
        sec = dict(fg_color='transparent', border_width=1, border_color=PAL['border'], text_color=PAL['text'],
                   hover_color=PAL['card2'], height=34, font=(FONT, 12))
        ctk.CTkButton(b, text='Fechar', width=90, command=self.destroy, **sec).pack(side='left', padx=4)
        ctk.CTkButton(b, text='Simular', width=90, command=self.simulate, **sec).pack(side='left', padx=4)
        ctk.CTkButton(b, text='Aplicar', width=100, height=34, font=(FONT, 12, 'bold'), command=self.apply,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).pack(side='left', padx=(4, 0))

    def _browse(self):
        p = filedialog.askopenfilename(title='Clipe de referência', filetypes=[('Vídeo', '*.MP4 *.mp4 *.MOV *.mov')])
        if p:
            self.ref.set(p)

    def _ref_path(self):
        r = self.ref.get().strip()
        if not r:
            return None
        return r if os.path.isabs(r) else os.path.join(self.app.src.get(), r)

    def _offset(self):
        o = self.offset.get().strip()
        return int(o) if o else None

    def _say(self, txt):
        self.result.delete('1.0', 'end')
        self.result.insert('end', txt)

    def simulate(self, apply=False):
        ref = self._ref_path()
        if not ref or not os.path.exists(ref):
            self._say('Escolha um clipe de referência válido.')
            return False
        try:
            off = self._offset()
        except ValueError:
            self._say('O início dos dados precisa ser um número inteiro de bytes.')
            return False
        c = self.c
        backup = (c.status, c.reference, c.detail, getattr(c, '_plan', None), dict(c.extra))

        def restore():
            c.status, c.reference, c.detail, c._plan, c.extra = backup

        try:
            mode = self.mode.get()
            if mode == 'reconstruir':
                if c.status == core.HEADER:
                    c.status = core.NO_MOOV
                core.plan_rebuild(c, [ref], off)
                self._say(c.detail + f'\nReferência: {os.path.basename(ref)}')
            elif mode == 'extrair':
                try:
                    core.plan_rebuild(c, [ref], off)
                    core.set_extract(c, [ref], 'layout reconhecido: vídeo e áudio completos')
                except Exception:
                    c._plan = None
                    core.set_extract(c, [ref], 'layout não reconhecido: só vídeo')
                self._say(c.detail + f'\nReferência (SPS/PPS e fps): {os.path.basename(ref)}')
            else:
                c.reference = ref
                c.extra['mode'] = 'cabecalho'
                c.detail = 'cabeçalho corrompido, índice íntegro — reparável (referência manual)'
                self._say(c.detail)
        except Exception as e:
            restore()
            self._say(f'Não foi possível com essa configuração:\n{e}')
            return False
        if not apply:
            restore()
        return True

    def apply(self):
        if self.simulate(apply=True):
            i = str(self.app.clips.index(self.c))
            if self.app.tree.exists(i):
                self.app.tree.item(i, values=self.app._row(self.c), tags=(kind(self.c),))
                self.app.tree.selection_set(i)
            self.app._metrics()
            self.app._update_actions()
            self.destroy()


def main():
    App().mainloop()


if __name__ == '__main__':
    main()
