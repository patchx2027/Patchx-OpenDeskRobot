#!/usr/bin/env python3
"""Explicit negotiation tests; trace actual backend I/O ordering independently."""
import errno
import json
import os
from pathlib import Path
import subprocess
import tempfile

MODE = '--capture-once-640x480-nv12'
REJECTS = ('format-width-adjust','format-height-adjust','format-fourcc-adjust',
           'format-plane-zero','format-plane-two','format-field-adjust','format-type-adjust',
           'format-bad-stride','format-size-zero','format-size-short','format-size-overflow',
           'format-gfmt-width-drift','format-gfmt-color-drift','format-gfmt-stride-drift',
           'format-gfmt-type-drift','format-sfmt-error','format-gfmt-error',
           'format-sfmt-eintr','format-gfmt-eintr','format-sfmt-cancel')

def verify(executable, prefix=()):
    cases=[]
    with tempfile.TemporaryDirectory() as directory:
        directory=Path(directory)
        def invoke(scenario,expected,mode=MODE):
            out=directory/scenario
            trace=directory/(scenario+'.trace.json')
            result=subprocess.run([*prefix,str(executable),mode,'/dev/video-fixture',str(out)],
                env=dict(os.environ,CAMERA_FIXTURE=scenario,CAMERA_TRACE=str(trace)),
                capture_output=True,text=True,timeout=12)
            assert result.returncode==expected,(scenario,result.returncode,result.stdout[:2048],result.stderr[:2048])
            data=json.loads(result.stdout)
            io=json.loads(trace.read_text())
            cases.append(dict(scenario=scenario,exit_code=result.returncode,first_errno=data['first_errno'],
                              phase=data['first_phase'],io=io))
            return data,io,out
        for scenario in ('format-success','format-padded','format-initial-raw'):
            data,io,out=invoke(scenario,0)
            assert data['saved'] and not data['first_errno'] and not data['cleanup_uncertain'],data
            assert (data['initial_format']['width'],data['initial_format']['height'])==(2592,1944)
            assert (data['width'],data['height'])==(640,480)
            assert data['requested_format']['field']==data['actual_format']['field']==1
            assert data['requested_format']['plane_count']==data['actual_format']['plane_count']==1
            assert data['format_verified'] and data['sfmt_attempts']==data['gfmt_verify_attempts']==1
            assert data['actual_format']==data['set_format_returned']
            assert data['mode']=='capture-once-640x480-nv12'
            assert io==dict(sfmt=1,gfmt_verify=1,reqbufs=1,streamon=1,requested_field=1,requested_planes=1),io
            stride=672 if scenario=='format-padded' else 640
            assert out.read_bytes()==bytes([42])*(stride*480*3//2)
            metadata=json.loads(Path(str(out)+'.json').read_text())
            assert metadata['actual_format']==data['actual_format'] and metadata['initial_format']==data['initial_format']
            assert data['device_close_attempts']==2 and data['munmap_attempts']==4
        for scenario in REJECTS:
            data,io,out=invoke(scenario,1)
            assert data['first_errno'] and not data['saved'] and not data['format_verified'],data
            assert data['actual_format'] is None and data['reqbufs_attempts']==0,data
            assert io['reqbufs']==io['streamon']==data['streamon_attempts']==data['munmap_attempts']==0,(scenario,data,io)
            assert data['device_close_attempts']==2 and data['streamoff_attempts']==0,data
            assert out.stat().st_size==0
            if scenario.endswith('eintr'):
                assert data['first_errno']==errno.ETIMEDOUT and data['elapsed_ms']<11000,data
            if scenario=='format-sfmt-cancel': assert data['first_errno']==errno.ECANCELED
        for scenario in ('zero-payload','combined-cleanup-error'):
            data,io,out=invoke(scenario,1)
            assert io['sfmt']==io['gfmt_verify']==io['reqbufs']==io['streamon']==1
            assert data['format_verified'] and data['device_close_attempts']==2
            if scenario=='zero-payload': assert not data['saved'] and out.stat().st_size==0
            else: assert data['saved'] and data['cleanup_uncertain'] and out.stat().st_size==460800
        for arguments in ([MODE],[MODE,'/dev/video-fixture'],[MODE,'relative','/tmp/out'],
                          [MODE,'/dev/video-fixture','relative'],[MODE,'/dev/video-fixture','/dev/video-fixture']):
            result=subprocess.run([*prefix,str(executable),*arguments],capture_output=True,timeout=12)
            assert result.returncode==2 and not result.stdout and not result.stderr,(arguments,result)
            cases.append(dict(scenario='arguments:'+repr(arguments),exit_code=2))
    return cases

if __name__=='__main__':
    import sys
    print(json.dumps(verify(Path(sys.argv[1]),sys.argv[2:]),indent=2))
