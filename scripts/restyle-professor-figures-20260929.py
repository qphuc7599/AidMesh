"""Restyle five existing result plots without rerunning or changing estimates."""
from pathlib import Path
import ast
import json
import matplotlib as mpl
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'overleaf/figs'
PREVIEW = ROOT / 'tmp/todo_20260929/plots'
PREVIEW.mkdir(parents=True, exist_ok=True)
BLUE = '#2878B5'
TEAL = '#159D8C'

def style():
    mpl.rcParams.update({'font.family':'DejaVu Sans','font.size':8,
        'axes.labelsize':8,'axes.titlesize':8,'xtick.labelsize':7.5,
        'ytick.labelsize':7.5,'legend.fontsize':7.5,'pdf.fonttype':42,
        'ps.fonttype':42})

def finish(fig, name):
    fig.savefig(OUT / (name+'.pdf'), bbox_inches='tight',pad_inches=.05)
    fig.savefig(PREVIEW / (name+'.png'), dpi=180,bbox_inches='tight',pad_inches=.05)
    plt.close(fig)

def panel(ax, letter, title=''):
    ax.text(0,1.04,'('+letter.lower()+')',transform=ax.transAxes,
        fontsize=8,fontweight='bold',va='bottom',color='#263238')

def namespace(path):
    ns={'__name__':'restyle_import','__file__':str(path)}
    exec(compile(path.read_text(encoding='utf8'),str(path),'exec'),ns)
    ns.update(OUT=OUT,finish=finish,panel_label=panel)
    style()
    return ns

def function_text(path,name):
    s=path.read_text(encoding='utf8');tree=ast.parse(s)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ast.get_source_segment(s,node)

def main():
    old=ROOT/'scripts/professor_plot_helpers_20260929.py'
    ns=namespace(old)
    src=function_text(old,'h1_figure')
    src=src.replace('color = TEAL if hm >= 0.05 else (GOLD if hm >= 0 else RED)','color = TEAL')
    src=src.replace('yy + 0.14','yy + 0.18').replace('yy - 0.14','yy - 0.18')
    src=src.replace('hh + 0.012','max(hh, rh) + 0.024')
    src='\n'.join(x for x in src.splitlines() if 'ax.set_title(' not in x)
    src='\n'.join(x for x in src.splitlines() if not any(k in x for k in ['"reversal", transform=', '"below +0.05 margin", transform=', '"supported envelope", transform=']))
    src=src.replace('len(ids)-0.3','len(ids)-0.05')
    exec(src,ns);ns['h1_figure']()
    src=function_text(old,'receipt_figure')
    src=src.replace('colors = {0.65: BLUE, 0.85: TEAL, 0.95: PURPLE}',
        'colors = {0.65: TEAL, 0.85: TEAL, 0.95: TEAL}')
    src=src.replace('Median receipt-delay reduction ({unit})  â†’','Receipt-delay reduction ({unit})')
    exec(src,ns);ns['receipt_figure']()

    h3=ROOT/'scripts/plot-h3-fixed-replication-20260928.py'
    ns=namespace(h3);ns['OUTPUT']=OUT/'allocation_evidence.pdf'
    src=function_text(h3,'main')
    src=src.replace('axes[0].set_title("(a) Full-box leximin fairness", loc="left")',"panel_label(axes[0], 'a')")
    src=src.replace('axes[1].set_title("(b) Fixed noninferiority decision", loc="left")',"panel_label(axes[1], 'b')")
    src=src.replace('"#245b85"','"#159D8C"')
    src=src.replace('plt.close(fig)',"fig.savefig(PREVIEW / 'allocation_evidence.png', dpi=180, bbox_inches='tight'); plt.close(fig)")
    ns['PREVIEW']=PREVIEW;exec(src,ns);ns['main']()

    ns=namespace(ROOT/'scripts/plot-submission-settlement-evidence.py')
    src=function_text(ROOT/'scripts/plot-submission-settlement-evidence.py','settlement_figure')
    src=src.replace('BLS share generation','BLS construction').replace('Sequential generation time (ms)','Sequential construction time (ms)').replace('BLS shares','BLS')
    src=src.replace('ax.text(a+b+75, i,', 'ax.text(4475, i + 0.34,')
    src=src.replace('va="center", fontsize=6.8', 'va="bottom", ha="right", fontsize=7.0')
    src=src.replace('ax.set_xlim(0, 4550)', 'ax.set_xlim(0, 4550)\n    ax.set_ylim(-0.5, 2.85)')
    exec(src,ns);ns['settlement_figure']()

    style()
    data=json.loads((ROOT/'results/external_validity_three_family_20260928/fixed_analysis.json').read_text())
    effects={(r['control'],r['family']):r for r in data['effects']}
    families=['ene5ai','sociopatterns','umass_dieselnet','all-three']
    labels=['ENE5AI','SocioPatterns','DieselNet','Family-balanced']
    fig,axs=plt.subplots(1,2,figsize=(7.25,2.65),sharey=True,layout='constrained')
    for ax,control,letter in zip(axs,['optimized_static','periodic_sync'],'ab'):
        for y,family in enumerate(families):
            r=effects[control,family];mean=r['mean'];lo,hi=r['ci95']
            ax.errorbar(mean,y,xerr=[[mean-lo],[hi-mean]],fmt='o',color=TEAL,capsize=3,ms=4,lw=1.2)
        ax.axvline(0,color='#555555',ls='--',lw=.8)
        ax.set_xlabel('Completion-rate difference');panel(ax,letter)
        ax.grid(axis='x',color='#dce5ea',lw=.55);ax.spines[['top','right']].set_visible(False)
    axs[0].set_yticks(range(4),labels);axs[0].invert_yaxis()
    finish(fig,'three_family_replication')

if __name__=='__main__':main()
