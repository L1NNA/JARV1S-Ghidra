from functools import partial
from json import dump
from re import M
import angr
import os
from tqdm import tqdm
import logging as log
from jvd.utils import write_gz_js, m_map
log.getLogger('angr').setLevel(log.CRITICAL)
log.getLogger('cle').setLevel(log.CRITICAL)


def eval_state(d, verbose=-1):
    d_vars = []
    d_vars_name = []
    d_vars_res = []
    for k, v in d.solver.get_variables():
        if k[0] == 'api':
            name = k[1]
        elif k[0] == 'reg':
            name = f'reg_{k[1]}'
        elif k[0] == 'mem':
            name = f'mem_{hex(k[1])[2:]}'
        elif k[0] == 'file':
            name = k[1]
        else:
            name = k[1]
        # quick hack for amd64/x86..
        # avoid stack/ip/segment registers
        if not k[1] in (0x38, 0x30, 0x40, 0x48, 0xb8):
            d_vars.append(v)
            d_vars_name.append(name)

    if verbose > 1:
        print('started', len(d_vars), len(d.solver.constraints))
    vals = d.solver._solver.batch_eval(d_vars, 1)[0]
    if verbose > 1:
        print('done', len(vals))
    for v, val in zip(d_vars, vals):
        try:
            if val != 0:
                bytez = val.to_bytes(v.size(), 'big')
                str_val = hex(val)
                h = str_val[2:]
                if len(h) >= 62:
                    str_val = bytez.decode(
                        'utf-8', errors='ignore'
                    ).strip().replace('\x00', '')
                d_vars_res.append(str_val)
        except (ValueError, Exception) as v:
            log.error(
                'Failed to evaluate variable ' + str(v))
            if verbose > 1:
                raise v
            pass
    return {
        'vars': d_vars_name,
        'vars_res': d_vars_res,
        'addr': list(d.history.bbl_addrs)
    }


def dump_sim(binary, function=None, tracelet=-1, overlap=False, loop=1, verbose=-1):
    p = angr.Project(binary, auto_load_libs=False)

    functions = []
    blocks = []
    binary = {
        'arch': p.arch.vex_archinfo,
        'sim_procedures': {
            k: [v.display_name] for k, v in p._sim_procedures.items()
        },
        'entry': p.entry,
    }

    cfg = p.analyses.CFGFast()
    for tar in tqdm(p.kb.functions.values()):
        if function:
            if not isinstance(function, list):
                function = [function]
            if not tar.name in function:
                continue
        paths = []
        endpoints = [
            b.addr
            # tar.get_block(b.addr).instruction_addrs[-1]
            for b in tar.endpoints]
        functions.append(
            {
                'addr': tar.addr,
                'name': tar.name,
                'bbs_len': len(list(tar.blocks)),
                'size': tar.size,
                # 'calls': [f.addr for f in tar.functions_called()],
                'bbs': [b.addr for b in tar.blocks],
                'eps': endpoints,
                'paths': paths
            }
        )

        for b in tar.blocks:
            ins = {i.address: {
                'addr': i.address, 'mne': i.mnemonic,
                'oprs': i.op_str, 'vex': []} for i in b.capstone.insns}
            blk = {
                'addr': b.addr,
                'f_addr': tar.addr,
                'size': b.size,
                'ins': sorted(ins.values(), key=lambda x: x['addr']),
            }
            addr = -1
            try:
                for s in b.vex.statements:
                    if hasattr(s, 'addr') and isinstance(s.addr, int):
                        addr = s.addr
                    if addr in ins:
                        ins[addr]['vex'].append(str(s))
            except:
                pass
            blocks.append(blk)

        try:
            if tracelet < 0:
                call_state = p.factory.call_state(
                    tar.addr,
                    # to get list of write/read actions
                    # `state.history.actions`
                    # add_options=angr.options.refs,
                )
                simgr = p.factory.simgr(call_state)
                simgr.use_technique(angr.exploration_techniques.LoopSeer(
                    cfg=cfg, functions=None, bound=loop))
                if verbose > 1:
                    print('running', len(blocks))
                # simgr.explore(find=endpoints, cfg=cfg)
                simgr.run()
                if verbose > 1:
                    print('done running')
                for d in simgr.deadended:
                    paths.append(eval_state(d, verbose=verbose))
            else:
                covered = set()
                for b in tar.blocks:
                    if not overlap and b.addr in covered:
                        continue
                    call_state = p.factory.call_state(
                        b.addr,
                    )
                    simgr = p.factory.simgr(call_state)
                    simgr.use_technique(angr.exploration_techniques.LoopSeer(
                        cfg=cfg, functions=None, bound=loop))
                    for _ in range(tracelet):
                        simgr.step()
                        # simgr.move(
                        #     from_stash='active', to_stash='deadended',
                        #     filter_func=lambda s: s.addr in endpoints)
                    for d in list(simgr.active) + list(simgr.deadended):
                        pt = eval_state(d, verbose=verbose)
                        covered.update(pt['addr'])
                        paths.append(pt)
        except (ValueError, Exception) as e:
            log.error(str(e))
            if verbose > 1:
                raise e

    return {'bin': binary, 'functions': functions, 'blocks': blocks}


def process_file(file, **kwargs):
    dump_file = file + '.vex.json.gz'
    if not os.path.exists(dump_file):
        write_gz_js(
            dump_sim(file, **kwargs),
            dump_file
        )
    return dump_file


def process_all(files, **kwargs):
    for _ in m_map(partial(process_file, **kwargs), files):
        pass
