#!/usr/bin/env python3
"""Second Take — interface gráfica (CustomTkinter).

A lógica de diagnóstico e reparo fica toda em `core`/`rebuild`; este módulo só
cuida da apresentação. O tema acompanha o modo claro/escuro do sistema e o
idioma (PT/EN) pode ser trocado na hora pelo seletor do cabeçalho.
"""
from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

try:  # arrastar e soltar (opcional: sem a biblioteca, o app segue só com os botões)
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # pragma: no cover
    DND_FILES = TkinterDnD = None

from . import __version__, core, i18n, updater
from .i18n import L, msg, t

REPO_URL = 'https://github.com/felipelimas8/secondtake'
ISSUES_URL = REPO_URL + '/issues'
LICENSE_URL = 'https://www.gnu.org/licenses/gpl-3.0.html'
RELEASES_URL = REPO_URL + '/releases/latest'

FONT = 'SF Pro Text' if sys.platform == 'darwin' else 'Segoe UI' if sys.platform == 'win32' else 'Helvetica'
MONO = 'SF Mono' if sys.platform == 'darwin' else 'Consolas' if sys.platform == 'win32' else 'Courier'

# paleta (claro, escuro)
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
STATUS_ICON = {'ok': '✓', 'warn': '●', 'info': '●', 'danger': '▲', 'muted': '·'}


def app_icon(size: int):
    """Ícone do app como CTkImage (ou None se a imagem não estiver disponível)."""
    try:
        from importlib import resources
        from PIL import Image
        with resources.files(__package__).joinpath('icon.png').open('rb') as fh:
            im = Image.open(fh)
            im.load()
        return ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
    except Exception:
        return None


def install_help() -> str:
    key = 'install.help_win' if sys.platform == 'win32' else 'install.help'
    return t(key, url=RELEASES_URL)


def install_label() -> str:
    return t('about.install_win' if sys.platform == 'win32' else 'about.install')


def open_folder(path: str):
    if sys.platform == 'win32':
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.run(['open', path])
    else:
        subprocess.run(['xdg-open', path])


def free_space(path: str) -> float:
    import shutil
    return shutil.disk_usage(path).free


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
    """Categoria visual do clipe: ok, warn, info ou danger."""
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


def secondary(**kw):
    """Estilo de botão secundário (contorno)."""
    d = dict(fg_color='transparent', border_width=1, border_color=PAL['border'], text_color=PAL['text'],
             hover_color=PAL['card2'])
    d.update(kw)
    return d


# ======================================================================= app

_DnDBase = TkinterDnD.DnDWrapper if TkinterDnD else object


class App(ctk.CTk, _DnDBase):
    def __init__(self):
        i18n.init()
        ctk.set_appearance_mode('system')
        ctk.set_default_color_theme('blue')
        super().__init__(fg_color=PAL['bg'])
        # tela de apresentação enquanto a janela é montada (some assim que ela fica pronta)
        self.withdraw()
        splash = None
        if not os.environ.get('SECONDTAKE_NO_SPLASH'):
            try:
                splash = Splash(self)
            except Exception:
                splash = None
        try:
            self._init_app()
        except Exception:
            if splash is not None:
                splash.destroy()
            raise
        if splash is not None:
            splash.finish(self._reveal)   # tempo mínimo de tela, depois mostra a janela e some com fade
        else:
            self._reveal()

    def _reveal(self):
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_force()

    def _init_app(self):
        self.dnd = False
        if TkinterDnD:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd = True
            except Exception:
                self.dnd = False
        self.geometry('1240x760')
        self.minsize(980, 600)
        self.clips: list[core.Clip] = []
        self.q = queue.Queue()
        self.busy = False
        self.repairing = False
        self._thumb_cache: dict = {}       # (arquivo, mtime, tamanho) -> [PNG]
        self._thumb_pending: set = set()
        self._thumb_want = None            # chave da prévia que o painel está esperando
        self._thumb_imgs: list = []        # referências às CTkImage (senão o Tk as descarta)
        self.cancel_evt = threading.Event()
        self.src = tk.StringVar()
        self.dst = tk.StringVar(value=os.path.expanduser('~/Movies/SecondTake'))
        self.dst_view = tk.StringVar()             # o que o rodapé mostra (pasta ou "pasta de cada clipe")
        self.beside = tk.BooleanVar(value=bool(i18n.load_config().get('save_beside', False)))
        self.dst.trace_add('write', lambda *_: self._sync_dst())
        self.only_bad = False
        self.do_verify = tk.BooleanVar(value=bool(core.ffmpeg_bin('ffprobe')))
        self.status_text = L('app.ready')
        self.subtitle_text = L('app.tagline', v=__version__)
        self._mode = None
        self.update_info = None
        self._style_ttk()
        self._build()
        self._install_menu()
        self.after(100, self._poll)
        self.after(2500, lambda: self.check_updates(manual=False))
        if sys.platform == 'win32':
            self.after(300, self._win_icon)

    def _win_icon(self):
        try:
            from importlib import resources
            with resources.as_file(resources.files(__package__).joinpath('icon.ico')) as p:
                self.iconbitmap(str(p))
        except Exception:
            pass

    # ------------------------------------------------------------ idioma
    def set_language(self, pref: str):
        """pref: 'auto' (segue o macOS), 'pt' ou 'en'."""
        before = i18n.get_lang()
        i18n.set_preference(pref)
        if hasattr(self, '_lang_var'):
            self._lang_var.set(pref)
        if i18n.get_lang() == before:
            return
        sel = set(self.tree.selection())
        search = self.search.get()
        for w in self.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self._build()
        self._install_menu()
        if search:
            self.search.insert(0, search)
        self._refresh()
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        self._update_actions()

    # ------------------------------------------------------------ layout
    def _build(self):
        f = (FONT, 13)
        self.title('Second Take')
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # cabeçalho
        head = ctk.CTkFrame(self, fg_color=PAL['card'], corner_radius=0, border_width=0)
        head.grid(row=0, column=0, sticky='ew')
        head.grid_columnconfigure(1, weight=1)
        icon = app_icon(44)
        if icon:
            logo = ctk.CTkLabel(head, text='', image=icon, width=44, height=44)
        else:
            logo = ctk.CTkLabel(head, text='T2', width=40, height=40, corner_radius=10,
                                fg_color=PAL['accent'], text_color='#FFFFFF', font=(FONT, 15, 'bold'))
        logo.grid(row=0, column=0, rowspan=2, padx=(20, 12), pady=14)
        ctk.CTkLabel(head, text='Second Take', font=(FONT, 18, 'bold'), text_color=PAL['text'],
                     anchor='w').grid(row=0, column=1, sticky='sw', pady=(14, 0))
        self.subtitle = ctk.CTkLabel(head, text=str(self.subtitle_text), font=(FONT, 12),
                                     text_color=PAL['muted'], anchor='w')
        self.subtitle.grid(row=1, column=1, sticky='nw', pady=(0, 14))
        self.btn_scan = ctk.CTkButton(head, text=t('app.scan_folder'), width=140, height=34, font=f,
                                      command=self.scan, **secondary())
        self.btn_scan.grid(row=0, column=2, rowspan=2, padx=6)
        self.btn_files = ctk.CTkButton(head, text=t('app.scan_files'), width=150, height=34, font=f,
                                       command=self.scan_selected, **secondary())
        self.btn_files.grid(row=0, column=3, rowspan=2, padx=(6, 20))
        ctk.CTkFrame(self, height=1, fg_color=PAL['border'], corner_radius=0).grid(row=0, column=0, sticky='sew')

        # faixa de atualização (só aparece quando há versão nova)
        self.banner = ctk.CTkFrame(self, fg_color=PAL['select'], corner_radius=10)
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_lbl = ctk.CTkLabel(self.banner, text='', font=(FONT, 13), text_color=PAL['text'], anchor='w')
        self.banner_lbl.grid(row=0, column=0, sticky='w', padx=(16, 8), pady=10)
        ctk.CTkButton(self.banner, text=t('upd.skip'), width=10, height=28, font=(FONT, 12), fg_color='transparent',
                      text_color=PAL['muted'], hover_color=PAL['card2'], command=self._update_skip).grid(
            row=0, column=1, padx=2)
        ctk.CTkButton(self.banner, text=t('upd.later'), width=10, height=28, font=(FONT, 12),
                      command=self._update_later, **secondary()).grid(row=0, column=2, padx=4)
        ctk.CTkButton(self.banner, text=t('upd.download'), width=90, height=28, font=(FONT, 12, 'bold'),
                      fg_color=PAL['accent'], hover_color=PAL['accent_h'], command=self._update_download).grid(
            row=0, column=3, padx=(4, 12))
        self._show_banner()

        # métricas
        met = ctk.CTkFrame(self, fg_color='transparent')
        met.grid(row=2, column=0, sticky='ew', padx=20, pady=(16, 8))
        self.metric = {}
        for i, (key, label, color) in enumerate((('total', 'app.m_total', 'text'), ('ok', 'app.m_ok', 'ok'),
                                                 ('fix', 'app.m_fix', 'warn'),
                                                 ('bad', 'app.m_bad', 'danger'))):
            met.grid_columnconfigure(i, weight=1, uniform='m')
            card = ctk.CTkFrame(met, fg_color=PAL['card'], corner_radius=10, border_width=1,
                                border_color=PAL['border'])
            card.grid(row=0, column=i, sticky='ew', padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            ctk.CTkLabel(card, text=t(label), font=(FONT, 12), text_color=PAL['muted'],
                         anchor='w').pack(anchor='w', padx=14, pady=(10, 0))
            v = ctk.CTkLabel(card, text='—', font=(FONT, 24, 'bold'), text_color=PAL[color], anchor='w')
            v.pack(anchor='w', padx=14, pady=(0, 10))
            self.metric[key] = v

        # filtros
        flt = ctk.CTkFrame(self, fg_color='transparent')
        flt.grid(row=3, column=0, sticky='ew', padx=20, pady=(4, 8))
        flt.grid_columnconfigure(1, weight=1)
        self.seg = ctk.CTkSegmentedButton(flt, values=[t('app.view_bad'), t('app.view_all')],
                                          command=self._on_view, font=(FONT, 12), height=30)
        self.seg.set(t('app.view_bad') if self.only_bad else t('app.view_all'))
        self.seg.grid(row=0, column=0, sticky='w')
        self.search = ctk.CTkEntry(flt, placeholder_text=t('app.search'), width=220, height=30,
                                   font=(FONT, 12))
        self.search.grid(row=0, column=2, sticky='e')
        self.search.bind('<KeyRelease>', lambda e: self._refresh())

        # tabela
        self.body = ctk.CTkFrame(self, fg_color=PAL['card'], corner_radius=10, border_width=1,
                                 border_color=PAL['border'])
        self.body.grid(row=4, column=0, sticky='nsew', padx=20)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        cols = ('status', 'action', 'audio', 'size', 'ref', 'detail')
        heads = ('app.col_status', 'app.col_action', 'app.col_audio', 'app.col_size', 'app.col_ref',
                 'app.col_detail')
        widths = (120, 95, 70, 80, 170, 300)
        self._style_ttk()
        self.tree = ttk.Treeview(self.body, columns=cols, show='tree headings', selectmode='extended',
                                 style='ST.Treeview')
        self.tree.heading('#0', text=t('app.col_clip'), anchor='w')
        self.tree.column('#0', width=230, stretch=False, anchor='w')
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=t(h), anchor='w')
            self.tree.column(c, width=w, stretch=c == 'detail', anchor='w')
        sb = ctk.CTkScrollbar(self.body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(2, 0), pady=2)
        sb.grid(row=0, column=1, sticky='ns', pady=6)
        self.tree.bind('<<TreeviewSelect>>', lambda e: (self._update_actions(), self._show_details()))

        # painel de detalhes (metadados do clipe selecionado)
        self.details = ctk.CTkFrame(self.body, fg_color=PAL['card2'], corner_radius=8, width=300)
        self.details.grid(row=0, column=2, sticky='nsew', padx=(6, 6), pady=6)
        self.details.grid_propagate(False)
        self.details.grid_columnconfigure(0, weight=1)
        self.tree.bind('<Double-1>', lambda e: self.advanced())
        self._tag_colors()

        # estado vazio
        self.empty = ctk.CTkFrame(self.body, fg_color='transparent')
        if self.dnd:
            ctk.CTkLabel(self.empty, text='⇣', font=(FONT, 40), text_color=PAL['muted']).pack()
        ctk.CTkLabel(self.empty, text=t('app.drop_title' if self.dnd else 'app.empty_title'), font=(FONT, 17, 'bold'),
                     text_color=PAL['text']).pack(pady=(0, 6))
        ctk.CTkLabel(self.empty, text=t('app.empty_body'), font=(FONT, 13), text_color=PAL['muted'],
                     justify='center').pack(pady=(0, 16))
        row = ctk.CTkFrame(self.empty, fg_color='transparent')
        row.pack()
        ctk.CTkButton(row, text=t('app.scan_files'), height=36, font=f, command=self.scan_selected,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).pack(side='left', padx=6)
        ctk.CTkButton(row, text=t('app.scan_folder'), height=36, font=f, command=self.scan,
                      **secondary()).pack(side='left', padx=6)

        # rodapé
        foot = ctk.CTkFrame(self, fg_color=PAL['card'], corner_radius=0)
        foot.grid(row=5, column=0, sticky='ew', pady=(14, 0))
        ctk.CTkFrame(self, height=1, fg_color=PAL['border'], corner_radius=0).grid(row=5, column=0, sticky='new',
                                                                                   pady=(14, 0))
        foot.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(foot, text=t('app.output'), font=(FONT, 12), text_color=PAL['muted']).grid(
            row=0, column=0, padx=(20, 8), pady=(12, 2), sticky='w')
        self.dst_lbl = ctk.CTkLabel(foot, textvariable=self.dst_view, font=(MONO, 12), text_color=PAL['text'],
                                    anchor='w', cursor='hand2')
        self.dst_lbl.grid(row=0, column=1, sticky='w', pady=(12, 2))
        self.dst_lbl.bind('<Button-1>', lambda e: self._pick_dst())
        self.btn_change = ctk.CTkButton(foot, text=t('app.change'), width=70, height=26, font=(FONT, 12),
                                        command=self._pick_dst, fg_color='transparent', text_color=PAL['accent'],
                                        hover_color=PAL['card2'], text_color_disabled=PAL['muted'])
        self.btn_change.grid(row=0, column=2, sticky='w', pady=(12, 2))
        ctk.CTkSwitch(foot, text=t('app.verify_switch'), variable=self.do_verify, font=(FONT, 12),
                      text_color=PAL['text']).grid(row=0, column=3, padx=12, pady=(12, 2))
        # destino: logo abaixo da pasta de saída, que é o que ela muda
        ctk.CTkSwitch(foot, text=t('app.beside_switch'), variable=self.beside, font=(FONT, 12),
                      text_color=PAL['text'], command=self._toggle_beside).grid(
            row=1, column=1, columnspan=3, sticky='w', pady=(2, 4))
        self._sync_dst()

        self.status = ctk.CTkLabel(foot, text=str(self.status_text), font=(FONT, 12), text_color=PAL['muted'],
                                   anchor='w')
        self.status.grid(row=2, column=0, columnspan=4, sticky='ew', padx=20, pady=(0, 2))
        self.prog = ctk.CTkProgressBar(foot, height=6)
        self.prog.set(0)
        self.prog.grid(row=3, column=0, columnspan=4, sticky='ew', padx=20, pady=(0, 14))

        acts = ctk.CTkFrame(foot, fg_color='transparent')
        acts.grid(row=0, column=4, rowspan=4, padx=(8, 20), pady=12, sticky='e')
        sec = secondary(height=36, font=f)
        self.btn_open = ctk.CTkButton(acts, text=t('app.open_out'), width=110, command=self._open_out, **sec)
        self.btn_open.pack(side='left', padx=4)
        self.btn_report = ctk.CTkButton(acts, text=t('app.export'), width=120, command=self.export, **sec)
        self.btn_report.pack(side='left', padx=4)
        self.btn_adv = ctk.CTkButton(acts, text=t('app.advanced'), width=110, command=self.advanced, **sec)
        self.btn_adv.pack(side='left', padx=4)
        self.btn_fix = ctk.CTkButton(acts, text=t('app.repair'), width=170, height=36, font=(FONT, 13, 'bold'),
                                     command=self.repair, fg_color=PAL['accent'], hover_color=PAL['accent_h'])
        self.btn_fix.pack(side='left', padx=(8, 0))
        self._refresh()
        self._show_details()
        self._setup_drop()

    # ------------------------------------------------------------ arrastar e soltar
    def _setup_drop(self):
        if not self.dnd:
            return
        for w in (self, self.body, self.tree, self.empty):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind('<<DropEnter>>', self._drop_enter)
                w.dnd_bind('<<DropLeave>>', self._drop_leave)
                w.dnd_bind('<<Drop>>', self._drop)
            except Exception:
                pass

    def _drop_enter(self, e):
        if not self.busy:
            self.body.configure(border_color=PAL['accent'], border_width=2)
        return e.action

    def _drop_leave(self, e):
        self.body.configure(border_color=PAL['border'], border_width=1)
        return e.action

    def _drop(self, e):
        self._drop_leave(e)
        if self.busy:
            return e.action
        self.open_paths(self.tk.splitlist(e.data))
        return e.action

    def open_paths(self, paths):
        """Pasta solta: analisa a pasta. Arquivos soltos: analisa só eles."""
        paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(core.VIDEO_EXT)]
        if dirs:
            d = dirs[0]
            self.src.set(d)
            self._set_subtitle(d)
            self._start_scan(L('app.scanning_folder'), lambda prog: core.scan(d, prog))
        elif files:
            base = os.path.commonpath([os.path.dirname(p) for p in files])
            self.src.set(base)
            self._set_subtitle(L('app.files_in', n=len(files), path=base))
            self._start_scan(L('app.scanning_files'), lambda prog: core.scan_files(files, prog))
        else:
            self._set_status(L('app.drop_none'))

    def _style_ttk(self):
        """Treeview é ttk. O tema 'clam' é obrigatório: o 'aqua' do macOS ignora cores e
        o alinhamento à esquerda dos títulos das colunas."""
        self._mode = ctk.get_appearance_mode()
        st = ttk.Style(self)
        if st.theme_use() != 'clam':
            try:
                st.theme_use('clam')
            except tk.TclError:
                pass
        bg, fg, head, sel = col('card'), col('text'), col('card2'), col('select')
        st.configure('ST.Treeview', background=bg, fieldbackground=bg, foreground=fg, rowheight=30,
                     borderwidth=0, relief='flat', font=(FONT, 12))
        st.configure('ST.Treeview.Heading', background=head, foreground=col('muted'), relief='flat',
                     borderwidth=0, font=(FONT, 11), padding=(8, 6), anchor='w')
        st.map('ST.Treeview.Heading', background=[('active', head), ('pressed', head)],
               relief=[('active', 'flat'), ('pressed', 'flat')])
        st.map('ST.Treeview', background=[('selected', sel)], foreground=[('selected', fg)])
        st.layout('ST.Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])

    def _tag_colors(self):
        # íntegros ficam discretos; o que precisa de atenção usa a cor de texto normal
        self.tree.tag_configure('ok', foreground=col('muted'))
        for k in ('warn', 'info', 'danger', 'muted'):
            self.tree.tag_configure(k, foreground=col('text'))

    def _show_details(self):
        for w in self.details.winfo_children():
            w.destroy()
        sel = self._selected()
        ctk.CTkLabel(self.details, text=t('meta.title'), font=(FONT, 13, 'bold'), text_color=PAL['text'],
                     anchor='w').grid(row=0, column=0, sticky='ew', padx=14, pady=(12, 2))
        if len(sel) != 1:
            ctk.CTkLabel(self.details, text=t('meta.none'), font=(FONT, 12), text_color=PAL['muted'], anchor='w',
                         justify='left', wraplength=260).grid(row=1, column=0, sticky='ew', padx=14, pady=4)
            return
        c = sel[0]
        ctk.CTkLabel(self.details, text=c.name, font=(MONO, 11), text_color=PAL['muted'], anchor='w').grid(
            row=1, column=0, sticky='ew', padx=14, pady=(0, 8))
        self._show_preview(c)                    # no topo: o painel não rola em janelas baixas
        rows = core.meta_rows(c)
        box = ctk.CTkFrame(self.details, fg_color='transparent')
        box.grid(row=4, column=0, sticky='nsew', padx=14)
        box.grid_columnconfigure(1, weight=1)
        for i, (k, v) in enumerate(rows):
            ctk.CTkLabel(box, text=k, font=(FONT, 11), text_color=PAL['muted'], anchor='nw', height=16).grid(
                row=i, column=0, sticky='nw', padx=(0, 10), pady=3)
            ctk.CTkLabel(box, text=v, font=(FONT, 11), text_color=PAL['text'], anchor='nw', justify='left',
                         wraplength=170, height=16).grid(row=i, column=1, sticky='nw', pady=3)

        def copy():
            self.clipboard_clear()
            self.clipboard_append(c.name + '\n' + '\n'.join(f'{k}: {v}' for k, v in rows))
        ctk.CTkButton(self.details, text=t('meta.copy'), width=80, height=26, font=(FONT, 11), command=copy,
                      **secondary()).grid(row=5, column=0, sticky='w', padx=14, pady=(10, 12))

    # ------------------------------------------------------------ prévia (3 quadros)
    def _show_preview(self, c):
        ctk.CTkLabel(self.details, text=t('meta.preview'), font=(FONT, 11, 'bold'), text_color=PAL['muted'],
                     anchor='w').grid(row=2, column=0, sticky='ew', padx=14, pady=(0, 4))
        self.thumb_box = ctk.CTkFrame(self.details, fg_color='transparent')
        self.thumb_box.grid(row=3, column=0, sticky='ew', padx=14, pady=(0, 10))
        self._thumb_want = None
        src = core.thumbnail_source(c)
        if not core.ffmpeg_bin():
            return self._thumb_note(t('meta.preview_noff'))
        if not src:
            return self._thumb_note(t('meta.preview_after'))
        try:
            st = os.stat(src)
        except OSError:
            return self._thumb_note(t('meta.preview_fail'))
        key = (src, st.st_mtime_ns, st.st_size)
        self._thumb_want = key
        self._thumb_log = 'log3' in str((c.extra.get('meta') or {}).get('xml_gamma', '')).lower().replace('-', '')
        if key in self._thumb_cache:
            return self._render_thumbs(self._thumb_cache[key])
        self._thumb_note(t('meta.preview_loading'))
        if key in self._thumb_pending:
            return
        self._thumb_pending.add(key)

        def work():
            try:
                pngs = core.thumbnails(src)
            except Exception:
                pngs = []
            self.q.put(('thumbs', key, pngs))
        threading.Thread(target=work, daemon=True).start()

    def _thumb_note(self, text):
        for w in self.thumb_box.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.thumb_box, text=text, font=(FONT, 11), text_color=PAL['muted'], anchor='w',
                     justify='left', wraplength=260).pack(anchor='w')

    def _render_thumbs(self, pngs):
        import io
        from PIL import Image
        if not pngs or not any(pngs):
            return self._thumb_note(t('meta.preview_fail'))
        for w in self.thumb_box.winfo_children():
            w.destroy()
        row = ctk.CTkFrame(self.thumb_box, fg_color='transparent')
        row.pack(anchor='w')
        self._thumb_imgs = []
        w_px = 84
        for i, data in enumerate(pngs):
            if not data:
                im = Image.new('RGB', (16, 9), (40, 40, 40))
            else:
                im = Image.open(io.BytesIO(data))
                im.load()
            h_px = max(1, round(w_px * im.height / im.width))
            img = ctk.CTkImage(light_image=im, dark_image=im, size=(w_px, h_px))
            self._thumb_imgs.append(img)
            ctk.CTkLabel(row, image=img, text='').pack(side='left', padx=(0 if i == 0 else 4, 0))
        if getattr(self, '_thumb_log', False):
            ctk.CTkLabel(self.thumb_box, text=t('meta.preview_log'), font=(FONT, 10), text_color=PAL['muted'],
                         anchor='w').pack(anchor='w', pady=(4, 0))

    def _on_view(self, value):
        self.only_bad = value == t('app.view_bad')
        self._refresh()

    def _show_empty(self, show: bool):
        if show:
            # centraliza na área da tabela (não no corpo inteiro, que inclui o painel Detalhes);
            # y=12 compensa metade do cabeçalho das colunas
            self.empty.place(in_=self.tree, relx=0.5, rely=0.5, y=12, anchor='center')
        else:
            self.empty.place_forget()

    def _set_status(self, text):
        self.status_text = text
        self.status.configure(text=str(text))

    def _set_subtitle(self, text):
        self.subtitle_text = text
        self.subtitle.configure(text=str(text))

    # ------------------------------------------------------------ menu / Sobre
    def _install_menu(self):
        """Barra de menus nativa: Second Take (Sobre, Ajustes ⌘,), Idioma e Ajuda."""
        bar = tk.Menu(self)
        self._lang_var = tk.StringVar(value=i18n.preference())
        if sys.platform == 'darwin':
            # menu do app (Second Take): itens entram logo abaixo de "Sobre o Second Take"
            app_menu = tk.Menu(bar, name='apple', tearoff=0)
            # o Tk não cria o "Sobre…" quando o app define o próprio menu do app: entra aqui sempre
            app_menu.add_command(label=t('about.title'), command=self.about)
            app_menu.add_separator()
            app_menu.add_command(label=t('upd.menu'), command=lambda: self.check_updates(manual=True))
            app_menu.add_separator()
            bar.add_cascade(menu=app_menu)
        if sys.platform == 'darwin':
            self.createcommand('tkAboutDialog', self.about)
            self.createcommand('tk::mac::ShowPreferences', self.preferences)
            self.createcommand('::tk::mac::ShowHelp', lambda: webbrowser.open(REPO_URL))
        else:
            file_menu = tk.Menu(bar, tearoff=0)
            file_menu.add_command(label=t('menu.prefs'), accelerator='Ctrl+,', command=self.preferences)
            file_menu.add_separator()
            file_menu.add_command(label=t('menu.quit'), command=self.destroy)
            bar.add_cascade(label=t('menu.file'), menu=file_menu)
            self.bind_all('<Control-comma>', lambda e: self.preferences())
        lang_menu = tk.Menu(bar, tearoff=0)
        lang_menu.add_radiobutton(label=t('prefs.auto'), value='auto', variable=self._lang_var,
                                  command=lambda: self.set_language('auto'))
        lang_menu.add_separator()
        for code in i18n.LANGS:
            lang_menu.add_radiobutton(label=i18n.LANG_NAMES[code], value=code, variable=self._lang_var,
                                      command=lambda c=code: self.set_language(c))
        bar.add_cascade(label=t('app.language'), menu=lang_menu)
        help_menu = tk.Menu(bar, tearoff=0, name='help')
        if sys.platform != 'darwin':
            help_menu.add_command(label=t('about.title'), command=self.about)
        help_menu.add_command(label=t('about.help_page'), command=lambda: webbrowser.open(REPO_URL))
        help_menu.add_command(label=t('about.issue'), command=lambda: webbrowser.open(ISSUES_URL))
        help_menu.add_command(label=install_label(), command=self._copy_install_help)
        if sys.platform != 'darwin':
            help_menu.add_separator()
            help_menu.add_command(label=t('upd.menu'), command=lambda: self.check_updates(manual=True))
        bar.add_cascade(label=t('about.menu_help'), menu=help_menu)
        self.config(menu=bar)

    def _copy_install_help(self):
        self.clipboard_clear()
        self.clipboard_append(install_help())
        messagebox.showinfo('Second Take', t('about.copied'))

    def preferences(self):
        PrefsDialog(self)

    # ------------------------------------------------------------ atualizações
    def check_updates(self, manual: bool):
        if manual:
            self._set_status(L('upd.checking'))

        def work():
            try:
                self.q.put(('update', updater.check(force=manual), manual, None))
            except Exception as e:
                self.q.put(('update', None, manual, e))
        threading.Thread(target=work, daemon=True).start()

    def _show_banner(self):
        info = self.update_info
        if info:
            self.banner_lbl.configure(text=f"{t('upd.available', v=info['version'])}  {t('upd.current', v=__version__)}")
            self.banner.grid(row=1, column=0, sticky='ew', padx=20, pady=(14, 0))
        else:
            self.banner.grid_forget()

    def _update_download(self):
        info = self.update_info
        if info:
            webbrowser.open(info.get('download') or info['page'])
        self._update_later()

    def _update_later(self):
        self.update_info = None
        self._show_banner()

    def _update_skip(self):
        if self.update_info:
            updater.skip(self.update_info['version'])
        self._update_later()

    def about(self):
        AboutDialog(self)

    # ------------------------------------------------------------ helpers
    def _sync_dst(self):
        """Rodapé: mostra a pasta de saída ou, com "Salvar junto dos originais", o destino por clipe."""
        beside = self.beside.get()
        self.dst_view.set(t('app.out_beside') if beside else self.dst.get())
        if hasattr(self, 'btn_change'):
            self.btn_change.configure(state='disabled' if beside else 'normal')
            self.dst_lbl.configure(cursor='arrow' if beside else 'hand2')

    def _toggle_beside(self):
        i18n.save_config(save_beside=bool(self.beside.get()))
        self._sync_dst()

    def _pick_dst(self):
        if self.beside.get():
            return
        d = filedialog.askdirectory(title=t('app.pick_out'), initialdir=self.dst.get())
        if d:
            self.dst.set(d)

    def _open_out(self):
        if self.beside.get():
            # a subpasta Second Take do último clipe reparado, ou da pasta analisada
            done = [c for c in self.clips if c.output]
            cands = [os.path.dirname(done[-1].output)] if done else []
            if self.src.get():
                cands += [os.path.join(self.src.get(), core.SAME_FOLDER_DIR), self.src.get()]
            d = next((x for x in cands if os.path.isdir(x)), None)
            if d:
                open_folder(d)
            return
        d = self.dst.get()
        sub = os.path.join(d, core.SAME_FOLDER_DIR)
        if self.src.get() and core.same_dir(d, self.src.get()) and os.path.isdir(sub):
            d = sub                              # saída = origem: os recuperados estão na subpasta
        os.makedirs(d, exist_ok=True)
        open_folder(d)

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
        label = t('app.repair_n' if n == 1 else 'app.repair_n_pl', n=n) if n else t('app.repair')
        if self.repairing:
            # durante o reparo, a ação principal vira Cancelar (continua sendo o único botão azul)
            stopping = self.cancel_evt.is_set()
            self.btn_fix.configure(text=t('app.cancel'), command=self.cancel,
                                   state='disabled' if stopping else 'normal')
        else:
            self.btn_fix.configure(text=label, command=self.repair,
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
        action = t(f'mode.{mode}') if mode and c.status in core.REPAIRABLE else '—'
        if c.audio_db is None:
            audio = '—'
        elif c.audio_db < -75:
            audio = t('app.audio_silent')
        else:
            audio = f'{c.audio_db:.0f} dB'
        verify, detail = str(c.verify or ''), str(c.detail or '')
        if verify and detail and not verify.startswith(('ERRO', 'ERROR')):
            diag = f'{verify} · {detail}'
        else:
            diag = verify or detail or '—'
        return (f'{STATUS_ICON[kind(c)]}  ' + t(f'status.{c.status}'), action, audio,
                human(c.size), os.path.basename(c.reference) or '—', diag)

    def _metrics(self):
        if not self.clips:
            for v in self.metric.values():
                v.configure(text='—')
            return
        ks = [kind(c) for c in self.clips]
        sep = '.' if i18n.get_lang() == 'pt' else ','
        self.metric['total'].configure(text=f'{len(self.clips):,}'.replace(',', sep))
        self.metric['ok'].configure(text=str(ks.count('ok')))
        self.metric['fix'].configure(text=str(ks.count('warn') + ks.count('info')))
        self.metric['bad'].configure(text=str(ks.count('danger')))

    def _refresh(self):
        if not hasattr(self, 'tree'):
            return
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        q = self.search.get().strip().lower()
        base = self.src.get()
        for i, c in enumerate(self.clips):
            if self.only_bad and c.status == core.OK:
                continue
            if q and q not in c.name.lower():
                continue
            rel = os.path.relpath(c.path, base) if base and c.path.startswith(base) else c.name
            self.tree.insert('', 'end', iid=str(i), text=rel, values=self._row(c), tags=(kind(c),))
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        self._show_empty(not self.clips and not self.busy)
        self._metrics()
        self._update_actions()

    def _select_repairable(self):
        ids = [str(i) for i, c in enumerate(self.clips) if self._auto(c) and self.tree.exists(str(i))]
        self.tree.selection_set(ids)
        self._update_actions()
        self._show_details()

    # ------------------------------------------------------------ ações
    def _start_scan(self, label, fn):
        self.clips = []
        self.busy = True
        self._refresh()
        self._show_empty(False)
        self._set_busy(True)
        self._set_status(label)

        def work():
            def prog(i, n, c):
                self.q.put(('scan', i, n, c.name))
            try:
                self.q.put(('scan_done', fn(prog)))
            except Exception as e:
                self.q.put(('error', msg(e)))
        threading.Thread(target=work, daemon=True).start()

    def scan(self):
        d = filedialog.askdirectory(title=t('app.pick_src'), initialdir=self.src.get() or None)
        if not d:
            return
        self.src.set(d)
        self._set_subtitle(d)
        self._start_scan(L('app.scanning_folder'), lambda prog: core.scan(d, prog))

    def scan_selected(self):
        paths = filedialog.askopenfilenames(title=t('app.pick_files'),
                                            filetypes=[(t('app.video'), '*.MP4 *.mp4 *.MOV *.mov *.RSV *.rsv')])
        if not paths:
            return
        base = os.path.commonpath([os.path.dirname(p) for p in paths])
        self.src.set(base)
        self._set_subtitle(L('app.files_in', n=len(paths), path=base))
        self._start_scan(L('app.scanning_files'), lambda prog: core.scan_files(list(paths), prog))

    def repair(self):
        todo = self._todo()
        if not todo:
            return
        beside = self.beside.get()
        out = self.dst.get()
        total = sum(c.size for c in todo)
        if beside:
            # cada clipe vai para <pasta dele>/Second Take: confere o espaço de cada disco
            per_disk: dict = {}
            try:
                for d, group in core.group_by_folder(todo).items():
                    dev = os.stat(d).st_dev
                    need_d, _ = per_disk.get(dev, (0, d))
                    per_disk[dev] = (need_d + core.space_needed(group), d)
                short = [(need_d, free_space(d)) for need_d, d in per_disk.values() if free_space(d) < need_d]
            except OSError as e:
                messagebox.showerror('Second Take', t('app.cant_write', path=t('app.out_beside'), err=e))
                return
            if short:
                need_d, free = short[0]
                messagebox.showerror('Second Take', t('app.no_space', need=human(need_d), free=human(free)))
                return
            where = t('app.out_beside')
        else:
            src_dirs = {os.path.dirname(os.path.abspath(c.path)) for c in todo}
            if self.src.get():
                src_dirs.add(self.src.get())
            # saída = origem vai sozinha para a subpasta Second Take; subpasta da origem: perguntar
            if core.out_inside_source(out, src_dirs):
                if not messagebox.askyesno('Second Take', t('app.out_inside')):
                    return
            need = core.space_needed(todo)          # extração bruta ocupa ~3x; os demais modos ~1x
            try:
                os.makedirs(out, exist_ok=True)
                free = free_space(out)
            except OSError as e:
                messagebox.showerror('Second Take', t('app.cant_write', path=out, err=e))
                return
            if free < need:
                messagebox.showerror('Second Take', t('app.no_space', need=human(need), free=human(free)))
                return
            where = out
        if not messagebox.askyesno('Second Take', t('app.confirm', n=len(todo), size=human(total), path=where)):
            return
        self.cancel_evt.clear()
        self.repairing = True
        self._set_busy(True)
        verify = self.do_verify.get()
        report = t('csv.report_name')
        cancel = self.cancel_evt

        def work():
            done = 0
            n_done = 0
            for c in todo:
                if cancel.is_set():
                    break

                def progress(b, n, base=done):
                    if cancel.is_set():
                        raise core.Cancelled()      # sai do meio da escrita; o .part é apagado
                    self.q.put(('fix', base + b, total))

                try:
                    self.q.put(('fix_start', c.name))
                    dest = os.path.dirname(os.path.abspath(c.path)) if beside else out
                    core.repair(c, dest, progress)
                    n_done += 1
                    if verify:
                        self.q.put(('verify', c.name))
                        core.verify(c, cancel=cancel)
                except core.Cancelled:
                    c.verify = L('verify.cancelled') if not c.output else c.verify
                except Exception as e:
                    c.verify = L('verify.error', err=msg(e))
                done += c.size
                self.q.put(('fix_one', c))
            if beside:
                # um relatório em cada <pasta>/Second Take, com os clipes daquela pasta
                folders = core.group_by_folder(todo)
                for d in folders:
                    group = [c for c in self.clips if os.path.dirname(os.path.abspath(c.path)) == d]
                    try:
                        rdir = os.path.join(d, core.SAME_FOLDER_DIR)
                        os.makedirs(rdir, exist_ok=True)
                        core.write_report(group, os.path.join(rdir, report))
                    except Exception:
                        pass
                shown = os.path.join(next(iter(folders)), core.SAME_FOLDER_DIR) if len(folders) == 1 \
                    else t('app.out_beside')
            else:
                shown = core.report_dir(out, todo)   # saída = origem: relatório na subpasta Second Take
                try:
                    os.makedirs(shown, exist_ok=True)
                    core.write_report(self.clips, os.path.join(shown, report))
                except Exception:
                    pass
            stopped = cancel.is_set()
            self.q.put(('fix_done', n_done if stopped else len(todo), shown, report, len(todo) if stopped else None))
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        """Pede o fim do lote: o clipe em andamento é interrompido e descartado; os já
        reparados ficam, e o relatório é gravado com o que foi feito."""
        if not self.repairing or self.cancel_evt.is_set():
            return
        self.cancel_evt.set()
        self._set_status(L('app.cancelling'))
        self._update_actions()

    def advanced(self):
        sel = self._selected()
        if len(sel) != 1:
            return
        c = sel[0]
        if c.status not in core.REPAIRABLE:
            messagebox.showinfo('Second Take', t('app.intact', name=c.name))
            return
        AdvancedDialog(self, c)

    def export(self):
        p = filedialog.asksaveasfilename(defaultextension='.csv', initialfile=t('csv.diag_name'))
        if p:
            core.write_report(self.clips, p)
            self._set_status(L('app.saved', path=p))

    # ------------------------------------------------------------ fila
    def _poll(self):
        if ctk.get_appearance_mode() != self._mode:
            self._style_ttk()
            self._tag_colors()
        try:
            while True:
                m = self.q.get_nowait()
                k = m[0]
                if k == 'scan':
                    _, i, n, name = m
                    self.prog.set(i / max(n, 1))
                    self._set_status(L('app.analyzing', i=i, n=n, name=name))
                elif k == 'scan_done':
                    self.clips = m[1]
                    self.busy = False
                    bad = any(c.status != core.OK for c in self.clips)
                    self.only_bad = bad and len(self.clips) > 20
                    self.seg.set(t('app.view_bad') if self.only_bad else t('app.view_all'))
                    self._refresh()
                    self._select_repairable()
                    n_fix = len(self._todo())
                    n_ext = sum(1 for c in self.clips if kind(c) == 'danger')
                    from .i18n import Join
                    self._set_status(Join(L('app.done_scan', n=len(self.clips)),
                                          L('app.ready_n', n=n_fix) if n_fix else L('app.nothing'),
                                          L('app.extract_n', n=n_ext) if n_ext else ''))
                    self.prog.set(1)
                    self._set_busy(False)
                elif k == 'fix_start':
                    self._set_status(L('app.repairing', name=m[1]))
                elif k == 'fix':
                    self.prog.set(m[1] / max(m[2], 1))
                elif k == 'verify':
                    self._set_status(L('app.verifying', name=m[1]))
                elif k == 'fix_one':
                    c = m[1]
                    i = str(self.clips.index(c))
                    if self.tree.exists(i):
                        self.tree.item(i, values=self._row(c), tags=(kind(c),))
                    if self._selected() == [c]:
                        self._show_details()          # prévia do arquivo recuperado logo após o reparo
                elif k == 'fix_done':
                    if m[4] is not None:
                        self._set_status(L('app.cancelled', n=m[1], total=m[4], report=m[3], path=m[2]))
                    else:
                        self._set_status(L('app.done_fix', n=m[1], report=m[3], path=m[2]))
                    self.prog.set(1)
                    self.repairing = False
                    self.cancel_evt.clear()
                    self._set_busy(False)
                elif k == 'thumbs':
                    _, key, pngs = m
                    self._thumb_pending.discard(key)
                    self._thumb_cache[key] = pngs
                    if len(self._thumb_cache) > 200:
                        self._thumb_cache.pop(next(iter(self._thumb_cache)))
                    if key == self._thumb_want and getattr(self, 'thumb_box', None) is not None \
                            and self.thumb_box.winfo_exists():
                        self._render_thumbs(pngs)
                elif k == 'update':
                    _, info, manual, err = m
                    if manual:
                        self._set_status(self.status_text if not isinstance(self.status_text, L)
                                         or self.status_text.key != 'upd.checking' else L('app.ready'))
                        if err is not None:
                            messagebox.showwarning('Second Take', t('upd.failed', err=err))
                        elif info is None:
                            messagebox.showinfo('Second Take', t('upd.up_to_date', v=__version__))
                    if info:
                        self.update_info = info
                        self._show_banner()
                elif k == 'error':
                    self.busy = False
                    messagebox.showerror('Second Take', str(m[1]))
                    self._set_status(L('app.scan_error'))
                    self._set_busy(False)
                    self._refresh()
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ======================================================================= apresentação

class Splash(tk.Toplevel):
    """Tela de apresentação sem barra de título, centralizada na tela, nas cores do tema do sistema.
    Mostra o ícone, o nome, a linha do app com a versão e a autoria."""

    W, H = 440, 300
    MIN_SECONDS = 1.2      # tempo mínimo na tela, contado desde que aparece (não soma ao carregamento)
    FADE_MS = 250

    def __init__(self, app):
        self._t0 = time.monotonic()
        self._job = None
        self._closing = False
        self._on_close = None
        dark = ctk.get_appearance_mode() == 'Dark'
        col = {k: v[1 if dark else 0] for k, v in PAL.items()}
        super().__init__(app, bg=col['border'])
        self.overrideredirect(True)
        try:
            self.attributes('-topmost', True)
        except tk.TclError:
            pass
        x = (self.winfo_screenwidth() - self.W) // 2
        y = (self.winfo_screenheight() - self.H) // 2 - 40
        self.geometry(f'{self.W}x{self.H}+{max(x, 0)}+{max(y, 0)}')
        card = tk.Frame(self, bg=col['card'])
        card.place(x=1, y=1, width=self.W - 2, height=self.H - 2)   # 1 px de borda
        box = ctk.CTkFrame(card, fg_color=col['card'], bg_color=col['card'], corner_radius=0)
        box.place(relx=0.5, rely=0.46, anchor='center')
        icon = app_icon(96)
        if icon:
            ctk.CTkLabel(box, text='', image=icon, fg_color=col['card']).pack(pady=(0, 10))
        ctk.CTkLabel(box, text='Second Take', font=(FONT, 24, 'bold'), text_color=col['text'],
                     fg_color=col['card']).pack()
        ctk.CTkLabel(box, text=t('app.tagline', v=__version__), font=(FONT, 13), text_color=col['muted'],
                     fg_color=col['card']).pack(pady=(2, 0))
        ctk.CTkLabel(card, text=t('about.by'), font=(FONT, 11), text_color=col['muted'],
                     fg_color=col['card']).place(relx=0.5, rely=1.0, y=-22, anchor='center')
        self.bind('<Button-1>', lambda e: self._close())   # um clique pula
        self.lift()
        self.update()

    def finish(self, on_close):
        """Chamado quando a janela principal está pronta: espera o que faltar do tempo mínimo."""
        self._on_close = on_close
        wait = max(0.0, self.MIN_SECONDS - (time.monotonic() - self._t0))
        self._job = self.after(int(wait * 1000), self._close)

    def _close(self):
        if self._closing or self._on_close is None:
            return
        self._closing = True
        if self._job:
            self.after_cancel(self._job)
        self._on_close()           # mostra a janela principal por baixo
        try:
            self.lift()
        except tk.TclError:
            return
        self._fade(1.0)

    def _fade(self, alpha):
        steps = 10
        alpha -= 1.0 / steps
        try:
            if alpha <= 0:
                self.destroy()
                return
            self.attributes('-alpha', alpha)
            self.after(self.FADE_MS // steps, self._fade, alpha)
        except tk.TclError:        # janela já fechada ou sem suporte a transparência
            try:
                self.destroy()
            except tk.TclError:
                pass


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


class PrefsDialog(_Dialog):
    """Ajustes (⌘,): idioma."""

    def __init__(self, app):
        super().__init__(app, t('prefs.title'), '500x260')
        f = ctk.CTkFrame(self, fg_color='transparent')
        f.pack(fill='both', expand=True, padx=24, pady=20)
        f.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(f, text=t('app.language'), font=(FONT, 13), text_color=PAL['text'], anchor='w').grid(
            row=0, column=0, sticky='w', padx=(0, 16))
        self._names = {'auto': t('prefs.auto'), **{c: i18n.LANG_NAMES[c] for c in i18n.LANGS}}
        self.choice = ctk.CTkOptionMenu(f, values=list(self._names.values()), font=(FONT, 13),
                                        dropdown_font=(FONT, 13), width=240, command=self._pick)
        self.choice.set(self._names[i18n.preference()])
        self.choice.grid(row=0, column=1, sticky='w')
        ctk.CTkLabel(f, text=t('prefs.lang_hint'), font=(FONT, 11), text_color=PAL['muted'], anchor='w',
                     justify='left', wraplength=360).grid(row=1, column=0, columnspan=2, sticky='w', pady=(10, 0))
        ctk.CTkLabel(f, text=t('prefs.updates'), font=(FONT, 13), text_color=PAL['text'], anchor='w').grid(
            row=2, column=0, sticky='w', padx=(0, 16), pady=(18, 0))
        self.auto_upd = tk.BooleanVar(value=updater.auto_enabled())
        ctk.CTkCheckBox(f, text=t('prefs.auto_update'), variable=self.auto_upd, font=(FONT, 12),
                        text_color=PAL['text'], command=lambda: updater.set_auto(self.auto_upd.get())).grid(
            row=2, column=1, sticky='w', pady=(18, 0))
        ctk.CTkButton(f, text=t('about.close'), width=90, height=32, font=(FONT, 12), command=self.destroy,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).grid(row=3, column=1, sticky='e',
                                                                                 pady=(18, 0))

    def _pick(self, name):
        code = next(c for c, n in self._names.items() if n == name)
        self.destroy()
        self.app.set_language(code)


class AboutDialog(_Dialog):
    """Janela Sobre: versão, o que faz, autoria, licença e ambiente."""

    def __init__(self, app):
        super().__init__(app, t('about.title'), '600x650')
        f = ctk.CTkFrame(self, fg_color='transparent')
        f.pack(fill='both', expand=True, padx=28, pady=22)
        icon = app_icon(88)
        if icon:
            ctk.CTkLabel(f, text='', image=icon).pack(pady=(0, 6))
        ctk.CTkLabel(f, text='Second Take', font=(FONT, 22, 'bold'), text_color=PAL['text']).pack()
        ctk.CTkLabel(f, text=t('about.version', v=__version__), font=(FONT, 12),
                     text_color=PAL['muted']).pack(pady=(0, 8))
        ctk.CTkLabel(f, text=t('about.tagline'), font=(FONT, 13), text_color=PAL['text'],
                     justify='center').pack()

        ff = core.ffmpeg_bin('ffprobe')
        self._rows = [
            (t('about.formats'), t('about.formats_v')),
            (t('about.recovers'), t('about.recovers_v')),
            (t('about.originals'), t('about.originals_v')),
            ('ffprobe', ff if ff else t('about.ffprobe_missing')),
            (t('about.system'), f'macOS {platform.mac_ver()[0]} · {platform.machine()}' if sys.platform == 'darwin'
             else f'Windows {platform.release()} ({platform.version()}) · {platform.machine()}'
             if sys.platform == 'win32' else f'{platform.system()} {platform.release()}'),
            ('Python', f'{platform.python_version()} · Tk {self.tk.call("info", "patchlevel")} · '
                       f'CustomTkinter {ctk.__version__}'),
        ]
        # rótulo e valor alinhados pela primeira linha (ambos ancorados no topo-esquerda)
        box = ctk.CTkFrame(f, fg_color=PAL['card2'], corner_radius=10)
        box.pack(fill='x', pady=16)
        box.grid_columnconfigure(0, minsize=96)
        box.grid_columnconfigure(1, weight=1)
        last = len(self._rows) - 1
        for i, (k, v) in enumerate(self._rows):
            pady = (12 if i == 0 else 3, 12 if i == last else 3)
            ctk.CTkLabel(box, text=k, font=(FONT, 12), text_color=PAL['muted'], anchor='nw',
                         justify='left', height=18).grid(row=i, column=0, sticky='nw', padx=(16, 8), pady=pady)
            ctk.CTkLabel(box, text=v, font=(FONT, 12), text_color=PAL['text'], anchor='nw', justify='left',
                         wraplength=400, height=18).grid(row=i, column=1, sticky='nw', padx=(0, 16), pady=pady)

        ctk.CTkLabel(f, text=t('about.by'), font=(FONT, 12), text_color=PAL['text']).pack()
        ctk.CTkLabel(f, text=t('about.legal'), font=(FONT, 11), text_color=PAL['muted'],
                     justify='center').pack(pady=(2, 10))
        links = ctk.CTkFrame(f, fg_color='transparent')
        links.pack()
        for key, url in (('about.github', REPO_URL), ('about.issue', ISSUES_URL), ('about.license', LICENSE_URL)):
            ctk.CTkButton(links, text=t(key), width=10, height=26, font=(FONT, 12), fg_color='transparent',
                          text_color=PAL['accent'], hover_color=PAL['card2'],
                          command=lambda u=url: webbrowser.open(u)).pack(side='left', padx=4)
        b = ctk.CTkFrame(f, fg_color='transparent')
        b.pack(pady=(14, 0))
        ctk.CTkButton(b, text=install_label(), height=32, font=(FONT, 12), command=self._install_help,
                      **secondary()).pack(side='left', padx=4)
        ctk.CTkButton(b, text=t('about.copy'), height=32, font=(FONT, 12), command=self._copy,
                      **secondary()).pack(side='left', padx=4)
        ctk.CTkButton(b, text=t('about.close'), height=32, width=90, font=(FONT, 12), command=self.destroy,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).pack(side='left', padx=4)

    def _install_help(self):
        """Copia as instruções de instalação para mandar a um colega."""
        self.clipboard_clear()
        self.clipboard_append(install_help())
        messagebox.showinfo('Second Take', t('about.copied'), parent=self)

    def _copy(self):
        txt = f'Second Take {__version__}\n' + '\n'.join(f'{k}: {v}' for k, v in self._rows)
        self.clipboard_clear()
        self.clipboard_append(txt)


class AdvancedDialog(_Dialog):
    """Controles manuais: modo, referência, offset dos dados e simulação."""

    def __init__(self, app: App, clip):
        super().__init__(app, t('adv.title', name=clip.name), '660x500')
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
        f.grid_columnconfigure(0, minsize=120)
        f.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(f, text=clip.name, font=(MONO, 14, 'bold'), text_color=PAL['text'], anchor='w').grid(
            row=0, column=0, columnspan=3, sticky='w')
        ctk.CTkLabel(f, text=t(f'status.{clip.status}'), font=(FONT, 12),
                     text_color=PAL[kind(clip)], anchor='w').grid(row=1, column=0, columnspan=3, sticky='w',
                                                                  pady=(0, 14))

        ctk.CTkLabel(f, text=t('adv.mode'), font=(FONT, 12), text_color=PAL['muted'], anchor='nw').grid(
            row=2, column=0, sticky='nw', pady=(2, 0))
        mf = ctk.CTkFrame(f, fg_color='transparent')
        mf.grid(row=2, column=1, columnspan=2, sticky='w')
        for val, key, enabled in (('cabecalho', 'adv.m_header', clip.status == core.HEADER),
                                  ('reconstruir', 'adv.m_rebuild', True),
                                  ('extrair', 'adv.m_extract', True)):
            ctk.CTkRadioButton(mf, text=t(key), value=val, variable=self.mode, font=(FONT, 12),
                               text_color=PAL['text'],
                               state='normal' if enabled else 'disabled').pack(anchor='w', pady=2)

        ctk.CTkLabel(f, text=t('adv.ref'), font=(FONT, 12), text_color=PAL['muted'], anchor='w').grid(
            row=3, column=0, sticky='w', pady=(14, 0))
        ctk.CTkComboBox(f, variable=self.ref, values=names[:300] or [''], font=(FONT, 12),
                        dropdown_font=(FONT, 12), height=30).grid(row=3, column=1, sticky='ew', pady=(14, 0))
        ctk.CTkButton(f, text=t('adv.other'), width=70, height=30, font=(FONT, 12), command=self._browse,
                      **secondary()).grid(row=3, column=2, padx=(8, 0), pady=(14, 0))

        ctk.CTkLabel(f, text=t('adv.offset'), font=(FONT, 12), text_color=PAL['muted'], anchor='w').grid(
            row=4, column=0, sticky='w', pady=(10, 0))
        ctk.CTkEntry(f, textvariable=self.offset, width=140, height=30, font=(MONO, 12)).grid(
            row=4, column=1, sticky='w', pady=(10, 0))
        ctk.CTkLabel(f, text=t('adv.offset_hint'), font=(FONT, 11), text_color=PAL['muted'], anchor='w').grid(
            row=5, column=1, columnspan=2, sticky='w')

        self.result = ctk.CTkTextbox(f, height=110, font=(FONT, 12), fg_color=PAL['card2'],
                                     text_color=PAL['text'], wrap='word', corner_radius=8)
        self.result.grid(row=6, column=0, columnspan=3, sticky='nsew', pady=14)
        self.result.insert('end', str(clip.detail or ''))
        f.grid_rowconfigure(6, weight=1)

        b = ctk.CTkFrame(f, fg_color='transparent')
        b.grid(row=7, column=0, columnspan=3, sticky='e')
        sec = secondary(height=34, font=(FONT, 12))
        ctk.CTkButton(b, text=t('about.close'), width=90, command=self.destroy, **sec).pack(side='left', padx=4)
        ctk.CTkButton(b, text=t('adv.simulate'), width=90, command=self.simulate, **sec).pack(side='left', padx=4)
        ctk.CTkButton(b, text=t('adv.apply'), width=100, height=34, font=(FONT, 12, 'bold'), command=self.apply,
                      fg_color=PAL['accent'], hover_color=PAL['accent_h']).pack(side='left', padx=(4, 0))

    def _browse(self):
        p = filedialog.askopenfilename(title=t('adv.pick_ref'),
                                       filetypes=[(t('app.video'), '*.MP4 *.mp4 *.MOV *.mov *.RSV *.rsv')])
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
        self.result.insert('end', str(txt))

    def simulate(self, apply=False):
        ref = self._ref_path()
        if not ref or not os.path.exists(ref):
            self._say(t('adv.need_ref'))
            return False
        try:
            off = self._offset()
        except ValueError:
            self._say(t('adv.need_int'))
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
                self._say(f"{c.detail}\n{t('adv.ref_line', name=os.path.basename(ref))}")
            elif mode == 'extrair':
                try:
                    core.plan_rebuild(c, [ref], off)
                    core.set_extract(c, [ref], L('diag.extract_full'))
                except Exception:
                    c._plan = None
                    core.set_extract(c, [ref], L('diag.extract_video'))
                self._say(f"{c.detail}\n{t('adv.ref_line_x', name=os.path.basename(ref))}")
            else:
                c.reference = ref
                c.extra['mode'] = 'cabecalho'
                c.detail = L('diag.header_manual')
                self._say(c.detail)
        except Exception as e:
            restore()
            self._say(t('adv.failed', err=msg(e)))
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


def _log_dir() -> str:
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Logs/Second Take')
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Second Take', 'Logs')
    return os.path.expanduser('~/.local/state/secondtake')


def main():
    """Abre o app. Se algo falhar na abertura, grava o erro em um log e tenta mostrá-lo numa janela,
    em vez de o app só piscar no Dock e fechar."""
    import faulthandler
    import traceback
    log = None
    try:
        os.makedirs(_log_dir(), exist_ok=True)
        log = os.path.join(_log_dir(), 'secondtake.log')
        fh = open(log, 'a', encoding='utf-8')
        fh.write(f'\n--- Second Take {__version__} · {platform.platform()} · Python {platform.python_version()}\n')
        fh.flush()
        faulthandler.enable(fh)          # registra até falhas fatais (segfault) do Tk
    except Exception:
        pass
    try:
        App().mainloop()
    except Exception:
        err = traceback.format_exc()
        try:
            with open(log, 'a', encoding='utf-8') as f:
                f.write(err)
        except Exception:
            pass
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Second Take', f'O Second Take não conseguiu abrir.\n\n{err[-1500:]}\n\nLog: {log}')
        except Exception:
            pass
        raise


if __name__ == '__main__':
    main()
