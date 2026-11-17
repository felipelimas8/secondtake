"""Gera os assets visuais: ícone do app (1024 px) e fundo da janela do DMG (1x e 2x).

Uso: python3 scripts/make_assets.py   (requer Pillow e a fonte Inter, se disponível)
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'assets')
ACCENT = (31, 111, 235)
INK = (31, 31, 29)
MUTED = (110, 110, 105)
BG = (247, 247, 245)


def font(size, weight='SemiBold'):
    for p in (f'/usr/share/fonts/opentype/inter/Inter-{weight}.otf',
              f'/Library/Fonts/Inter-{weight}.otf',
              '/System/Library/Fonts/SFNS.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def icon():
    """Claquete "TAKE 2" sobre squircle escuro (estilo ícone do macOS). Desenha em 2x e reduz."""
    from PIL import ImageFilter
    SS, S = 2, 1024
    N = S * SS

    def p(v):
        return int(v * SS)

    img = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    m, r = p(100), p(225)
    sh = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([m, m + p(18), N - m, N - m + p(18)], r, fill=(0, 0, 0, 90))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(p(28))))
    grad = Image.new('RGBA', (N, N))
    gd = ImageDraw.Draw(grad)
    c1, c2 = (56, 60, 68), (20, 22, 26)
    for y in range(N):
        t = y / N
        gd.line([(0, y), (N, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)) + (255,))
    mask = Image.new('L', (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, N - m, N - m], r, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    x0, x1 = p(240), p(784)
    slate = (246, 246, 243)
    d.rounded_rectangle([x0, p(440), x1, p(800)], p(40), fill=slate)
    # braço fixo (preto e branco)
    d.rounded_rectangle([x0, p(372), x1, p(440)], p(14), fill=(28, 30, 34))
    for i in range(5):
        xa = x0 + i * p(110)
        d.polygon([(xa + p(22), p(440)), (xa + p(77), p(440)), (xa + p(112), p(372)), (xa + p(57), p(372))],
                  fill=slate)
    # braço móvel, aberto (preto e amarelo)
    arm = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arm)
    ad.rounded_rectangle([x0, p(282), x1, p(350)], p(14), fill=(28, 30, 34))
    for i in range(5):
        xa = x0 + i * p(110)
        ad.polygon([(xa + p(22), p(350)), (xa + p(77), p(350)), (xa + p(112), p(282)), (xa + p(57), p(282))],
                   fill=(255, 196, 0))
    img.alpha_composite(arm.rotate(14, center=(x0, p(372)), resample=Image.BICUBIC))
    # dobradiça
    d = ImageDraw.Draw(img)
    d.ellipse([x0 - p(10), p(352), x0 + p(30), p(392)], fill=(90, 92, 98))
    # "TAKE 2"
    d.text((x0 + p(44), p(468)), 'TAKE', font=font(p(64), 'SemiBold'), fill=(125, 125, 120))
    for y in (p(575), p(655), p(735)):
        d.rounded_rectangle([x0 + p(44), y, x0 + p(250), y + p(8)], p(4), fill=(205, 205, 200))
    f = font(p(330), 'Bold')
    w = d.textlength('2', font=f)
    d.text((x0 + p(395) - w / 2, p(445)), '2', font=f, fill=ACCENT)

    out = img.resize((S, S), Image.LANCZOS)
    out.save(os.path.join(OUT, 'icon_1024.png'))
    # cópia pequena empacotada com o app (cabeçalho e janela Sobre)
    pkg = os.path.join(HERE, '..', 'src', 'secondtake', 'icon.png')
    out.resize((256, 256), Image.LANCZOS).save(pkg)
    # Windows: .ico com vários tamanhos (app, atalhos, instalador e ícone da janela)
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    out.save(os.path.join(OUT, 'icon.ico'), sizes=ico_sizes)
    out.save(os.path.join(HERE, '..', 'src', 'secondtake', 'icon.ico'), sizes=ico_sizes)
    return out


def icon_beta(base):
    """Variante do Second Take Beta: o mesmo ícone com uma faixa laranja "BETA" no canto."""
    S = base.size[0]
    band = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    h = int(S * 0.11)
    cx = cy = int(S * 0.765)
    d.rectangle([-S, cy - h // 2, 2 * S, cy + h // 2], fill=(255, 122, 26, 255))
    f = font(int(h * 0.62), 'Bold')
    w = d.textlength('BETA', font=f)
    d.text((cx - w / 2, cy - h * 0.40), 'BETA', font=f, fill=(255, 255, 255))
    band = band.rotate(45, center=(cx, cy), resample=Image.BICUBIC)
    # só dentro do squircle do ícone
    m, r = 100 * S // 1024, 225 * S // 1024
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], r, fill=255)
    band.putalpha(Image.composite(band.getchannel('A'), Image.new('L', (S, S), 0), mask))
    out = base.copy()
    out.alpha_composite(band)
    out.save(os.path.join(OUT, 'icon_beta_1024.png'))
    out.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, '..', 'src', 'secondtake', 'icon_beta.png'))
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    out.save(os.path.join(OUT, 'icon_beta.ico'), sizes=ico_sizes)
    out.save(os.path.join(HERE, '..', 'src', 'secondtake', 'icon_beta.ico'), sizes=ico_sizes)
    return out


def dmg_background(scale):
    """Fundo bilíngue da janela do instalador: ícones no alto, passos em PT e EN embaixo."""
    W, H = 680 * scale, 580 * scale
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    s = scale
    d.text((40 * s, 28 * s), 'Second Take', font=font(26 * s, 'Bold'), fill=INK)
    d.text((40 * s, 62 * s), 'Arraste para Aplicativos  ·  Drag to Applications', font=font(14 * s, 'Regular'),
           fill=MUTED)
    # seta entre os ícones (ícones centrados em x=170 e x=510, y=200)
    y = 200 * s
    d.line([(255 * s, y), (420 * s, y)], fill=(190, 190, 185), width=4 * s)
    d.polygon([(420 * s, y - 12 * s), (440 * s, y), (420 * s, y + 12 * s)], fill=(190, 190, 185))
    # caixa de instruções, duas colunas
    bx, by, bw = 40 * s, 296 * s, W - 80 * s
    d.rounded_rectangle([bx, by, bx + bw, by + 168 * s], radius=12 * s, fill=(255, 255, 255),
                        outline=(226, 226, 222), width=max(1, s))
    d.line([(bx + bw // 2, by + 16 * s), (bx + bw // 2, by + 132 * s)], fill=(232, 232, 228), width=max(1, s))
    cols = [
        ('Na primeira vez que abrir', [
            '1. O Mac avisa que não verificou o app:',
            '    feche o aviso (não mova para o Lixo).',
            '2. Duplo clique em “Abrir Ajustes de',
            '    Segurança” abaixo › Abrir Mesmo Assim.',
            '3. Pronto. Só precisa fazer uma vez.',
        ]),
        ('The first time you open it', [
            '1. macOS says it could not verify the app:',
            '    close it (don’t move it to the Trash).',
            '2. Double-click “Abrir Ajustes de',
            '    Segurança” below › Open Anyway.',
            '3. Done. You only need to do this once.',
        ]),
    ]
    for ci, (title, lines) in enumerate(cols):
        x = bx + 18 * s + ci * (bw // 2)
        d.text((x, by + 14 * s), title, font=font(13 * s, 'SemiBold'), fill=INK)
        for i, ln in enumerate(lines):
            d.text((x, by + (40 + i * 20) * s), ln, font=font(11 * s, 'Regular'), fill=INK)
    d.text((bx + 18 * s, by + 144 * s), 'Terminal:  xattr -cr "/Applications/Second Take.app"',
           font=font(10 * s, 'Regular'), fill=MUTED)
    name = 'dmg_background.png' if scale == 1 else 'dmg_background@2x.png'
    img.save(os.path.join(OUT, name), dpi=(72 * scale, 72 * scale))


def social_preview():
    """Imagem de prévia de links (GitHub "Social preview" e página de download), 1280×640."""
    W, H = 1280, 640
    img = Image.new('RGB', (W, H), (24, 26, 30))
    d = ImageDraw.Draw(img)
    icon = Image.open(os.path.join(OUT, 'icon_1024.png')).resize((360, 360), Image.LANCZOS)
    img.paste(icon, (90, 140), icon)
    d.text((500, 175), 'Second Take', font=font(92, 'Bold'), fill=(245, 245, 242))
    d.text((504, 290), 'Recuperação de clipes de câmera corrompidos', font=font(31, 'Regular'), fill=(200, 200, 195))
    d.text((504, 338), 'Recovery for corrupted camera clips', font=font(30, 'Regular'), fill=(140, 140, 135))
    d.text((504, 420), 'Sony XAVC S  ·  macOS e Windows  ·  grátis e open source', font=font(26, 'Regular'),
           fill=(76, 141, 246))
    img.save(os.path.join(OUT, 'social_preview.png'))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    icon_beta(icon())
    dmg_background(1)
    dmg_background(2)
    social_preview()
    print('assets gerados em', os.path.abspath(OUT))
