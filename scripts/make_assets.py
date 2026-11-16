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
    return out


def dmg_background(scale):
    W, H = 680 * scale, 480 * scale
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    s = scale
    d.text((40 * s, 30 * s), 'Second Take', font=font(26 * s, 'Bold'), fill=INK)
    d.text((40 * s, 64 * s), 'Arraste o app para a pasta Aplicativos', font=font(15 * s, 'Regular'), fill=MUTED)
    # seta entre os ícones (ícones centrados em x=170 e x=510, y=200)
    y = 200 * s
    d.line([(255 * s, y), (420 * s, y)], fill=(190, 190, 185), width=4 * s)
    d.polygon([(420 * s, y - 12 * s), (440 * s, y), (420 * s, y + 12 * s)], fill=(190, 190, 185))
    # caixa de instruções
    bx, by = 40 * s, 292 * s
    d.rounded_rectangle([bx, by, W - 40 * s, H - 24 * s], radius=12 * s, fill=(255, 255, 255),
                        outline=(226, 226, 222), width=max(1, s))
    d.text((bx + 20 * s, by + 16 * s), 'Na primeira vez que abrir', font=font(14 * s, 'SemiBold'), fill=INK)
    steps = [
        '1.  O Mac avisa que não conseguiu verificar o app. Feche o aviso (não mova para o Lixo).',
        '2.  Abra Ajustes do Sistema › Privacidade e Segurança.',
        '3.  Role até o fim e clique em “Abrir Mesmo Assim”. Só precisa fazer uma vez.',
    ]
    for i, t in enumerate(steps):
        d.text((bx + 20 * s, by + (44 + i * 24) * s), t, font=font(12 * s, 'Regular'), fill=INK)
    d.text((bx + 20 * s, by + 128 * s), 'Ou, no Terminal:  xattr -cr "/Applications/Second Take.app"',
           font=font(11 * s, 'Regular'), fill=MUTED)
    name = 'dmg_background.png' if scale == 1 else 'dmg_background@2x.png'
    img.save(os.path.join(OUT, name), dpi=(72 * scale, 72 * scale))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    icon()
    dmg_background(1)
    dmg_background(2)
    print('assets gerados em', os.path.abspath(OUT))
