from pathlib import Path
import sys

# Patch NativeAdrenoSubmit.cpp in the CI build tree to record early-return points.
# Usage: python3 patch_submit_fail.py <path-to-NativeAdrenoSubmit.cpp>

path = Path(sys.argv[1])
text = path.read_text()

anchor = 'extern "C" {volatile uint32_t native_adreno_submit_ibs[4];}'
addition = '''extern "C" {volatile uint32_t native_adreno_submit_ibs[4];}
extern "C" {volatile uint32_t native_adreno_submit_fail[8];}  /* [0]=reason [1]=line [2]=handle [3]=bytes [4]=iova_lo [5]=iova_hi [6]=vm_iova_lo [7]=vm_iova_hi */'''
if text.count(anchor) != 1:
    raise SystemExit('submit_ibs anchor not found')
text = text.replace(anchor, addition, 1)

helper = '''#define SUBMIT_FAIL(reason, line, handle, bytes, iova, vm_iova, status) \\
    do { native_adreno_submit_fail[0]=reason; native_adreno_submit_fail[1]=line; \\
         native_adreno_submit_fail[2]=(uint32_t)(handle); native_adreno_submit_fail[3]=(uint32_t)(bytes); \\
         native_adreno_submit_fail[4]=(uint32_t)(uint64_t)(iova); native_adreno_submit_fail[5]=(uint32_t)((uint64_t)(iova)>>32); \\
         native_adreno_submit_fail[6]=(uint32_t)(uint64_t)(vm_iova); native_adreno_submit_fail[7]=(uint32_t)((uint64_t)(vm_iova)>>32); \\
         return status; } while(0)
'''
marker = 'IOReturn NativeAdrenoSubmit::submit(NativeAdrenoVm *vm,const NativeAdrenoSubmitBuffer *buffers,size_t count,'
if text.count(marker) != 1:
    raise SystemExit('submit marker not found')
text = text.replace(marker, helper + marker, 1)

repls = [
    ('if(!fence)return kIOReturnBadArgument;*fence=0;',
     'if(!fence) SUBMIT_FAIL(1, __LINE__, 0, 0, 0, 0, kIOReturnBadArgument); *fence=0;'),
    ('if(!attached_ || poisoned_)return kIOReturnNotReady;',
     'if(!attached_ || poisoned_) SUBMIT_FAIL(2, __LINE__, 0, 0, 0, 0, kIOReturnNotReady);'),
    ('if(vm->unusable())return kIOReturnNotPermitted;',
     'if(vm->unusable()) SUBMIT_FAIL(3, __LINE__, 0, 0, 0, 0, kIOReturnNotPermitted);'),
    ('if(!buffers || !count || count>SubmitMaxBuffers)return kIOReturnBadArgument;',
     'if(!buffers || !count || count>SubmitMaxBuffers) SUBMIT_FAIL(4, __LINE__, buffers?buffers[0].handle:0, count?buffers[0].bytes:0, 0, 0, kIOReturnBadArgument);'),
    ('if(!buffer.handle || !buffer.bytes || (buffer.bytes&3) || buffer.bytes>SubmitMaxIbBytes)\n            return kIOReturnBadArgument;',
     'if(!buffer.handle || !buffer.bytes || (buffer.bytes&3) || buffer.bytes>SubmitMaxIbBytes)\n            SUBMIT_FAIL(5, __LINE__, buffer.handle, buffer.bytes, buffer.iova, 0, kIOReturnBadArgument);'),
    ('IOReturn status=vm_->describe(buffer.handle,&iova,&bytes,&permissions);\n        if(status)return status;',
     'IOReturn status=vm_->describe(buffer.handle,&iova,&bytes,&permissions);\n        if(status) SUBMIT_FAIL(6, __LINE__, buffer.handle, buffer.bytes, buffer.iova, iova, status);'),
    ('if(!(permissions&ArmSmmu4KRead) || buffer.iova<iova ||\n            buffer.iova-iova>bytes || buffer.bytes>bytes-(buffer.iova-iova))return kIOReturnBadArgument;',
     'if(!(permissions&ArmSmmu4KRead) || buffer.iova<iova ||\n            buffer.iova-iova>bytes || buffer.bytes>bytes-(buffer.iova-iova)) SUBMIT_FAIL(7, __LINE__, buffer.handle, buffer.bytes, buffer.iova, iova, kIOReturnBadArgument);'),
    ('if(AdrenoPM4Type7(SubmitIndirectBuffer,SubmitIbWords,&header)!=AdrenoPM4Success)\n            return poison();',
     'if(AdrenoPM4Type7(SubmitIndirectBuffer,SubmitIbWords,&header)!=AdrenoPM4Success)\n            SUBMIT_FAIL(8, __LINE__, buffer.handle, buffer.bytes, buffer.iova, 0, poison());'),
    ('IOReturn swept=retireCompleted();\n    if(swept)return swept;',
     'IOReturn swept=retireCompleted();\n    if(swept) SUBMIT_FAIL(9, __LINE__, 0, 0, 0, 0, swept);'),
    ('if(!job)return kIOReturnNoMemory;',
     'if(!job) SUBMIT_FAIL(10, __LINE__, 0, 0, 0, 0, kIOReturnNoMemory);'),
    ('IOReturn status=vm_->holdJob(handles[i]);\n        if(status) {\n            while(i) {vm_->retireJobAfterCompletion(handles[--i]);++stats_.references_retired;}\n            return status;\n        }',
     'IOReturn status=vm_->holdJob(handles[i]);\n        if(status) {\n            while(i) {vm_->retireJobAfterCompletion(handles[--i]);++stats_.references_retired;}\n            SUBMIT_FAIL(11, __LINE__, handles[i], 0, 0, 0, status);\n        }'),
    ('if(status) {\n        for(size_t i=0;i<handle_count;++i) {vm_->retireJobAfterCompletion(handles[i]);++stats_.references_retired;}\n        return status;\n    }',
     'if(status) {\n        for(size_t i=0;i<handle_count;++i) {vm_->retireJobAfterCompletion(handles[i]);++stats_.references_retired;}\n        SUBMIT_FAIL(12, __LINE__, 0, 0, 0, 0, status);\n    }'),
    ('status=useVm(vm,clock,index);\n    if(status) {\n        for(size_t i=0;i<handle_count;++i) {vm_->retireJobAfterCompletion(handles[i]);++stats_.references_retired;}\n        return status;\n    }',
     'status=useVm(vm,clock,index);\n    if(status) {\n        for(size_t i=0;i<handle_count;++i) {vm_->retireJobAfterCompletion(handles[i]);++stats_.references_retired;}\n        SUBMIT_FAIL(13, __LINE__, 0, 0, 0, 0, status);\n    }'),
    ('if(status) {\n        if(!value)fence_issued_=base;\n        /* A poisoned ring may already have consumed the WPTR write, so the\n         * referenced buffers cannot be retired here. */\n        if(target->poisoned()) {\n            *job=Job{};++stats_.poisoned_jobs;\n            for(size_t i=0;i<handle_count;++i)job->handles[i]=handles[i];\n            job->count=handle_count;job->queue=queue;job->active=true;job->vm_next=vm_->nextHandle();job->vm=vm;\n            return poison();\n        }\n        while(handle_count) {vm_->retireJobAfterCompletion(handles[--handle_count]);++stats_.references_retired;}\n        /* No room in the ring yet: the caller waits for an earlier job and submits again. */\n        return status==kIOReturnNoResources?kIOReturnBusy:status;\n    }',
     'if(status) {\n        if(!value)fence_issued_=base;\n        /* A poisoned ring may already have consumed the WPTR write, so the\n         * referenced buffers cannot be retired here. */\n        if(target->poisoned()) {\n            *job=Job{};++stats_.poisoned_jobs;\n            for(size_t i=0;i<handle_count;++i)job->handles[i]=handles[i];\n            job->count=handle_count;job->queue=queue;job->active=true;job->vm_next=vm_->nextHandle();job->vm=vm;\n            SUBMIT_FAIL(14, __LINE__, 0, 0, 0, 0, poison());\n        }\n        while(handle_count) {vm_->retireJobAfterCompletion(handles[--handle_count]);++stats_.references_retired;}\n        /* No room in the ring yet: the caller waits for an earlier job and submits again. */\n        SUBMIT_FAIL(15, __LINE__, 0, 0, 0, 0, status==kIOReturnNoResources?kIOReturnBusy:status);\n    }'),
]

applied = 0
for old, new in repls:
    if old in text:
        text = text.replace(old, new, 1)
        applied += 1

if applied == 0:
    raise SystemExit('no submit early-return points were patched')

path.write_text(text)
print('patched', applied, 'early-return points in', path)
