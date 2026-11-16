#!/usr/bin/env python3
"""Second Take — interface Tkinter."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import core

COLORS = {core.OK: '#2e7d32', core.HEADER: '#b26a00', core.NO_MOOV: '#c62828',
          core.EMPTY: '#c62828', core.UNKNOWN: '#6a1b9a'}


def human(n):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {u}' if u != 'B' else f'{n} B'
        n /= 1024
    return f'{n:.1f} PB'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'Second Take {__version__}')
        self.geometry('1180x640')
        self.minsize(900, 480)
        self.clips: list[core.Clip] = []
        self.q = queue.Queue()
        self.busy = False
        self.src = tk.StringVar()
        self.dst = tk.StringVar(value=os.path.expanduser('~/Movies/SecondTake'))
        self.only_bad = tk.BooleanVar(value=False)
        self.do_verify = tk.BooleanVar(value=bool(core.ffmpeg_bin('ffprobe')))
        self._build()
        self.after(100, self._poll)

    # ---------------------------------------------------------------- UI
    def _build(self):
        pad = {'padx': 8, 'pady': 4}
        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        for row, (label, var, cmd) in enumerate((
                ('Origem (brutas):', self.src, self._pick_src),
                ('Saída (reparados):', self.dst, self._pick_dst))):
            ttk.Label(top, text=label, width=18).grid(row=row, column=0, sticky='w')
            ttk.Entry(top, textvariable=var).grid(row=row, column=1, sticky='ew', padx=4)
            ttk.Button(top, text='Escolher…', command=cmd).grid(row=row, column=2)
        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(self)
        bar.pack(fill='x', **pad)
        self.btn_scan = ttk.Button(bar, text='1 · Analisar', command=self.scan)
        self.btn_scan.pack(side='left')
        self.btn_files = ttk.Button(bar, text='Analisar arquivos…', command=self.scan_selected)
        self.btn_files.pack(side='left', padx=(6, 0))
        self.btn_fix = ttk.Button(bar, text='2 · Reparar selecionados', command=self.repair, state='disabled')
        self.btn_fix.pack(side='left', padx=6)
        ttk.Checkbutton(bar, text='Verificar com ffprobe', variable=self.do_verify).pack(side='left', padx=6)
        ttk.Checkbutton(bar, text='Mostrar só problemas', variable=self.only_bad,
                        command=self._refresh).pack(side='left', padx=6)
        self.btn_adv = ttk.Button(bar, text='Avançado…', command=self.advanced, state='disabled')
        self.btn_adv.pack(side='left', padx=6)
        self.btn_report = ttk.Button(bar, text='Exportar CSV', command=self.export, state='disabled')
        self.btn_report.pack(side='right')
        self.btn_open = ttk.Button(bar, text='Abrir saída', command=self._open_out)
        self.btn_open.pack(side='right', padx=6)

        cols = ('status', 'camera', 'size', 'audio', 'ref', 'detail', 'verify')
        heads = ('Status', 'Câmera', 'Tamanho', 'Áudio (pico)', 'Referência', 'Diagnóstico', 'Verificação')
        widths = (170, 90, 80, 90, 210, 330, 220)
        frame = ttk.Frame(self)
        frame.pack(fill='both', expand=True, **pad)
        self.tree = ttk.Treeview(frame, columns=cols, show='tree headings', selectmode='extended')
        self.tree.heading('#0', text='Arquivo')
        self.tree.column('#0', width=260, stretch=False)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, stretch=c in ('detail', 'verify'))
        for st, col in COLORS.items():
            self.tree.tag_configure(st, foreground=col)
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        bottom = ttk.Frame(self)
        bottom.pack(fill='x', **pad)
        self.prog = ttk.Progressbar(bottom, mode='determinate')
        self.prog.pack(fill='x')
        self.status = ttk.Label(bottom, text='Escolha a pasta de origem e clique em Analisar.')
        self.status.pack(anchor='w', pady=(4, 0))

    def _pick_src(self):
        d = filedialog.askdirectory(title='Pasta de brutas')
        if d:
            self.src.set(d)

    def _pick_dst(self):
        d = filedialog.askdirectory(title='Pasta de saída')
        if d:
            self.dst.set(d)

    def _open_out(self):
        d = self.dst.get()
        if os.path.isdir(d):
            subprocess.run(['open', d] if sys.platform == 'darwin' else ['xdg-open', d])

    def _set_busy(self, b):
        self.busy = b
        st = 'disabled' if b else 'normal'
        self.btn_scan.config(state=st)
        self.btn_files.config(state=st)
        self.btn_fix.config(state='disabled' if b or not self._repairable() else 'normal')
        self.btn_report.config(state='disabled' if b or not self.clips else 'normal')
        self.btn_adv.config(state='disabled' if b or not self.clips else 'normal')

    def _repairable(self):
        return [c for c in self.clips if c.status in core.REPAIRABLE and c.reference]

    def _row(self, c):
        return (c.status, c.camera or '—', human(c.size),
                '' if c.audio_db is None else f'{c.audio_db} dBFS',
                os.path.basename(c.reference), c.detail, c.verify)

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.clips):
            if self.only_bad.get() and c.status == core.OK:
                continue
            rel = os.path.relpath(c.path, self.src.get()) if self.src.get() else c.name
            self.tree.insert('', 'end', iid=str(i), text=rel, values=self._row(c), tags=(c.status,))
        for i, c in enumerate(self.clips):
            if c.status in core.REPAIRABLE and c.reference and self.tree.exists(str(i)):
                self.tree.selection_add(str(i))

    # ---------------------------------------------------------------- ações
    def scan(self):
        src = self.src.get()
        if not os.path.isdir(src):
            messagebox.showerror('Second Take', 'Escolha uma pasta de origem válida.')
            return
        self.clips = []
        self.tree.delete(*self.tree.get_children())
        self._set_busy(True)

        def work():
            def prog(i, n, c):
                self.q.put(('scan', i, n, c.name))
            try:
                clips = core.scan(src, prog)
                self.q.put(('scan_done', clips))
            except Exception as e:
                self.q.put(('error', str(e)))
        threading.Thread(target=work, daemon=True).start()

    def scan_selected(self):
        paths = filedialog.askopenfilenames(
            title='Escolha os clipes corrompidos',
            filetypes=[('Vídeo', '*.MP4 *.mp4 *.MOV *.mov')])
        if not paths:
            return
        self.src.set(os.path.commonpath([os.path.dirname(p) for p in paths]))
        self.clips = []
        self.tree.delete(*self.tree.get_children())
        self._set_busy(True)

        def work():
            def prog(i, n, c):
                self.q.put(('scan', i, n, c.name))
            try:
                self.q.put(('scan_done', core.scan_files(list(paths), prog)))
            except Exception as e:
                self.q.put(('error', str(e)))
        threading.Thread(target=work, daemon=True).start()

    def repair(self):
        sel = [self.clips[int(i)] for i in self.tree.selection()]
        todo = [c for c in sel if c.status in core.REPAIRABLE and c.reference]
        if not todo:
            messagebox.showinfo('Second Take', 'Nenhum arquivo reparável selecionado.')
            return
        out = self.dst.get()
        if os.path.abspath(out).startswith(os.path.abspath(self.src.get()) + os.sep):
            if not messagebox.askyesno('Second Take', 'A saída está dentro da pasta de origem. Continuar?'):
                return
        total = sum(c.size for c in todo)
        if not messagebox.askyesno('Second Take',
                                   f'Reparar {len(todo)} arquivo(s), {human(total)} de cópia em:\n{out}?'):
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
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showinfo('Second Take', 'Selecione um único arquivo para o modo Avançado.')
            return
        c = self.clips[int(sel[0])]
        if c.status not in core.REPAIRABLE:
            messagebox.showinfo('Second Take', f'{c.name} está com status {c.status} — nada a ajustar.')
            return
        AdvancedDialog(self, c)

    def export(self):
        p = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='relatorio_xavc.csv')
        if p:
            core.write_report(self.clips, p)
            self.status.config(text=f'Relatório salvo em {p}')

    # ---------------------------------------------------------------- fila
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'scan':
                    _, i, n, name = msg
                    self.prog.config(maximum=n, value=i)
                    self.status.config(text=f'Analisando {i}/{n}: {name}')
                elif kind == 'scan_done':
                    self.clips = msg[1]
                    self._refresh()
                    counts = {}
                    for c in self.clips:
                        counts[c.status] = counts.get(c.status, 0) + 1
                    txt = ' · '.join(f'{k}: {v}' for k, v in sorted(counts.items()))
                    self.status.config(text=f'{len(self.clips)} arquivos — {txt}')
                    self._set_busy(False)
                elif kind == 'fix_start':
                    self.status.config(text=f'Reparando {msg[1]}…')
                elif kind == 'fix':
                    self.prog.config(maximum=msg[2], value=msg[1])
                elif kind == 'verify':
                    self.status.config(text=f'Verificando {msg[1]}…')
                elif kind == 'fix_one':
                    c = msg[1]
                    i = str(self.clips.index(c))
                    if self.tree.exists(i):
                        self.tree.item(i, values=self._row(c))
                elif kind == 'fix_done':
                    self.status.config(text=f'{msg[1]} arquivo(s) reparado(s) em {msg[2]} · relatorio_reparo.csv gerado')
                    self._set_busy(False)
                elif kind == 'error':
                    messagebox.showerror('Second Take', msg[1])
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll)


class AdvancedDialog(tk.Toplevel):
    """Controles manuais: referência, offset dos dados e simulação."""

    def __init__(self, app: App, clip):
        super().__init__(app)
        self.app, self.c = app, clip
        self.title(f'Avançado — {clip.name}')
        self.geometry('720x330')
        self.transient(app)
        refs = [r for r in app.clips if r.status == core.OK]
        self.ref_paths = [r.path for r in refs]
        names = [os.path.relpath(r.path, app.src.get()) for r in refs]
        self.ref = tk.StringVar(value=os.path.relpath(clip.reference, app.src.get()) if clip.reference else '')
        self.offset = tk.StringVar(value='')
        self.mode = tk.StringVar(value=clip.extra.get('mode') or
                                 ('cabecalho' if clip.status == core.HEADER else 'reconstruir'))
        f = ttk.Frame(self, padding=12)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text=f'Status: {clip.status}').grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(f, text='Modo:').grid(row=1, column=0, sticky='w', pady=6)
        mf = ttk.Frame(f)
        mf.grid(row=1, column=1, sticky='w')
        ttk.Radiobutton(mf, text='Trocar cabeçalho (moov íntegro)', value='cabecalho', variable=self.mode,
                        state='normal' if clip.status == core.HEADER else 'disabled').pack(side='left')
        ttk.Radiobutton(mf, text='Reconstruir índice', value='reconstruir',
                        variable=self.mode).pack(side='left', padx=8)
        ttk.Radiobutton(mf, text='Extração bruta (.h264/.wav)', value='extrair',
                        variable=self.mode).pack(side='left')
        ttk.Label(f, text='Referência:').grid(row=2, column=0, sticky='w')
        self.cb = ttk.Combobox(f, textvariable=self.ref, values=names, width=60)
        self.cb.grid(row=2, column=1, sticky='ew')
        ttk.Button(f, text='Outro arquivo…', command=self._browse).grid(row=2, column=2, padx=4)
        ttk.Label(f, text='Início dos dados:').grid(row=3, column=0, sticky='w', pady=6)
        ttk.Entry(f, textvariable=self.offset, width=14).grid(row=3, column=1, sticky='w')
        ttk.Label(f, text='bytes — vazio = automático (262144 nas Sony XAVC S)',
                  foreground='#777').grid(row=4, column=1, sticky='w')
        self.result = tk.Text(f, height=5, wrap='word')
        self.result.grid(row=5, column=0, columnspan=3, sticky='nsew', pady=8)
        self.result.insert('end', clip.detail)
        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=3, sticky='e')
        ttk.Button(b, text='Simular', command=self.simulate).pack(side='left')
        ttk.Button(b, text='Aplicar', command=self.apply).pack(side='left', padx=6)
        ttk.Button(b, text='Fechar', command=self.destroy).pack(side='left')
        f.columnconfigure(1, weight=1)
        f.rowconfigure(5, weight=1)

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
            self._say('Início dos dados precisa ser um número inteiro.')
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
                c.detail = 'cabeçalho corrompido, moov íntegro — reparável (referência manual)'
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
                self.app.tree.item(i, values=self.app._row(self.c))
                self.app.tree.selection_set(i)
            self.app._set_busy(False)
            self.destroy()


def main():
    App().mainloop()


if __name__ == '__main__':
    main()
