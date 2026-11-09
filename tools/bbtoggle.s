# bbtoggle: a tray switch that turns bluebox on and off, in x86-64 Windows assembly.
#
#   Left-click the tray dot   toggle (gray = off, green = on)
#   Middle-click              copy the latest transcript to the clipboard
#   Right-click               menu: last 5 transcripts (click one to copy it), Open transcripts
#                             folder, Turn on/off, Quit
#
# Transcripts come from %USERPROFILE%\.bluebox\transcripts.jsonl (one JSON object per line, written by
# voice_daemon.py). Nothing here deletes anything; the folder item opens %USERPROFILE%\.bluebox (the log
# plus the audio\ recordings).
#
# On  = starts `pythonw voice_daemon.py` and `pythonw hotkey.py` (tap Ctrl to dictate)
#       from the repo this exe lives in (<repo>\tools\bbtoggle.exe).
# Off = terminates both. If either dies by itself the dot goes back to gray.
# Quit also turns bluebox off. One copy at a time. It starts off.
# Both children live in a kill-on-close Job Object, so they die with this exe even if it is killed
# from Task Manager (no orphaned daemon or hotkey listener left behind).
#
# Needs pythonw on PATH with pynput installed (hotkey.py's requirement).
#
# Build (GNU binutils for mingw-w64, e.g. in WSL: apt install binutils-mingw-w64-x86-64 mingw-w64-x86-64-dev):
#   x86_64-w64-mingw32-as tools/bbtoggle.s -o bbtoggle.o
#   x86_64-w64-mingw32-ld -e start --subsystem windows -o tools/bbtoggle.exe bbtoggle.o \
#       -L/usr/x86_64-w64-mingw32/lib -lkernel32 -luser32 -lshell32 -lgdi32
#
# No C runtime: it calls kernel32/user32/shell32/gdi32 directly.

.intel_syntax noprefix

.set WM_DESTROY, 0x0002
.set WM_TIMER,   0x0113
.set WM_TRAY,    0x0401
.set WM_LBUP,    0x0202
.set WM_RBUP,    0x0205
.set WM_MBUP,    0x0208

.data
cls_name:   .asciz "bbtoggle"
mutex_name: .asciz "bbtoggle-single-instance"
tip_off:    .asciz "bluebox: off (click to turn on)"
tip_on:     .asciz "bluebox: on (click to turn off)"
s_toggle:   .asciz "Turn on / off"
s_quit:     .asciz "Quit"
s_py:       .asciz "pythonw.exe \""
s_daemon:   .asciz "\\voice_daemon.py\""
s_hotkey:   .asciz "\\hotkey.py\""
s_up:       .asciz "USERPROFILE"
s_bbdir:    .asciz "\\.bluebox"
s_tj:       .asciz "\\transcripts.jsonl"
s_ktime:    .asciz "\"time\": \""
s_ktext:    .asciz "\"text\": \""
s_none:     .asciz "(no transcripts yet)"
s_openf:    .asciz "Open transcripts folder"
s_open:     .asciz "open"
s_copied:   .asciz "Copied to clipboard"
s_bbname:   .asciz "bluebox"
.align 8
sinfo:       .long 104                  # STARTUPINFOA.cb
            .fill 100, 1, 0

.section .bss
.align 16
exepath:    .skip 264                  # exe path, then trimmed to the repo root
cmdbuf:     .skip 600
nid:        .skip 528                  # NOTIFYICONDATAA
wc:         .skip 80                   # WNDCLASSEXA
msg:        .skip 64
ii:         .skip 32                   # ICONINFO
pix:        .skip 1024                 # 16x16 BGRA
mask:       .skip 32
pi1:        .skip 24
pi2:        .skip 24
pt:         .skip 8
hwnd:       .skip 8
hinst:      .skip 8
hicon_on:   .skip 8
hicon_off:  .skip 8
state:      .skip 8
hjob:       .skip 8
jbuf:       .skip 144                  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
home:       .skip 264
dpath:      .skip 300                  # %USERPROFILE%\.bluebox
tpath:      .skip 320                  # ...\transcripts.jsonl
ents:       .skip 48                   # 5 line pointers into fbuf, newest first
nents:      .skip 8
partial:    .skip 8                    # 1 if fbuf holds only the tail of the file
bytesread:  .skip 8
l8:         .skip 128                  # menu label, UTF-8
lbl:        .skip 512                  # menu label, UTF-16
u8:         .skip 4200                 # one decoded transcript, UTF-8
wbuf:       .skip 8400                 # ...and as UTF-16 for the clipboard
fbuf:       .skip 65600                # tail of transcripts.jsonl

.text
.globl start

# rdi = dst, rsi = src; copies the string, leaves rdi on the terminator.
cpy:
    mov al, [rsi]
    mov [rdi], al
    test al, al
    jz 1f
    inc rsi
    inc rdi
    jmp cpy
1:  ret

# ecx = color (0xAARRGGBB) -> rax = HICON, a filled 16x16 dot
make_icon:
    push rbx
    push rsi
    push rdi
    sub rsp, 64
    mov ebx, ecx
    lea rdi, [rip+pix]
    xor esi, esi
.Ly:
    xor r10d, r10d
.Lx:
    lea eax, [rsi*2-15]
    imul eax, eax
    lea edx, [r10*2-15]
    imul edx, edx
    add eax, edx
    xor edx, edx
    cmp eax, 225
    jg .Lw
    mov edx, ebx
.Lw:
    mov [rdi], edx
    add rdi, 4
    inc r10d
    cmp r10d, 16
    jl .Lx
    inc esi
    cmp esi, 16
    jl .Ly
    mov ecx, 16
    mov edx, 16
    mov r8d, 1
    mov r9d, 32
    lea rax, [rip+pix]
    mov [rsp+32], rax
    call CreateBitmap
    mov rdi, rax                       # color bitmap
    mov ecx, 16
    mov edx, 16
    mov r8d, 1
    mov r9d, 1
    lea rax, [rip+mask]
    mov [rsp+32], rax
    call CreateBitmap
    mov rsi, rax                       # mask bitmap
    lea rax, [rip+ii]
    mov dword ptr [rax], 1
    mov [rax+16], rsi
    mov [rax+24], rdi
    mov rcx, rax
    call CreateIconIndirect
    mov rbx, rax
    mov rcx, rsi
    call DeleteObject
    mov rcx, rdi
    call DeleteObject
    mov rax, rbx
    add rsp, 64
    pop rdi
    pop rsi
    pop rbx
    ret

# ecx = NIM_ADD(0) / NIM_MODIFY(1) / NIM_DELETE(2)
update_tray:
    push rbx
    sub rsp, 32
    mov ebx, ecx
    lea rdi, [rip+nid]
    mov dword ptr [rdi], 528
    mov rax, [rip+hwnd]
    mov [rdi+8], rax
    mov dword ptr [rdi+16], 1
    mov dword ptr [rdi+20], 7          # NIF_MESSAGE | NIF_ICON | NIF_TIP
    mov dword ptr [rdi+24], WM_TRAY
    lea rsi, [rip+tip_off]
    mov rax, [rip+hicon_off]
    cmp dword ptr [rip+state], 0
    je 1f
    lea rsi, [rip+tip_on]
    mov rax, [rip+hicon_on]
1:  mov [rdi+32], rax
    add rdi, 40
    call cpy
    mov ecx, ebx
    lea rdx, [rip+nid]
    call Shell_NotifyIconA
    add rsp, 32
    pop rbx
    ret

# rbx = PROCESS_INFORMATION*; terminate and close if running
kill1:
    sub rsp, 40
    mov rcx, [rbx]
    test rcx, rcx
    jz 1f
    xor edx, edx
    call TerminateProcess
    mov rcx, [rbx]
    call CloseHandle
    mov rcx, [rbx+8]
    call CloseHandle
    mov qword ptr [rbx], 0
    mov qword ptr [rbx+8], 0
1:  add rsp, 40
    ret

stop_all:
    push rbx
    sub rsp, 32
    lea rbx, [rip+pi1]
    call kill1
    lea rbx, [rip+pi2]
    call kill1
    mov dword ptr [rip+state], 0
    add rsp, 32
    pop rbx
    ret

# rsi = script suffix string, rbx = PROCESS_INFORMATION*
spawn:
    sub rsp, 88
    push rsi
    lea rdi, [rip+cmdbuf]
    lea rsi, [rip+s_py]
    call cpy
    lea rsi, [rip+exepath]
    call cpy
    pop rsi
    call cpy
    xor ecx, ecx
    lea rdx, [rip+cmdbuf]
    xor r8d, r8d
    xor r9d, r9d
    mov qword ptr [rsp+32], 0          # bInheritHandles
    mov qword ptr [rsp+40], 0x08000000 # CREATE_NO_WINDOW
    mov qword ptr [rsp+48], 0          # environment
    lea rax, [rip+exepath]
    mov [rsp+56], rax                  # working dir = repo root
    lea rax, [rip+sinfo]
    mov [rsp+64], rax
    mov [rsp+72], rbx
    call CreateProcessA
    test eax, eax
    jz 1f
    mov rcx, [rip+hjob]
    mov rdx, [rbx]                     # PROCESS_INFORMATION.hProcess
    call AssignProcessToJobObject
1:  add rsp, 88
    ret

start_all:
    push rbx
    sub rsp, 32
    lea rbx, [rip+pi1]
    lea rsi, [rip+s_daemon]
    call spawn
    lea rbx, [rip+pi2]
    lea rsi, [rip+s_hotkey]
    call spawn
    cmp qword ptr [rip+pi1], 0
    je 1f
    cmp qword ptr [rip+pi2], 0
    je 1f
    mov dword ptr [rip+state], 1
    jmp 2f
1:  call stop_all
2:  add rsp, 32
    pop rbx
    ret

toggle:
    sub rsp, 40
    cmp dword ptr [rip+state], 0
    jne 1f
    call start_all
    jmp 2f
1:  call stop_all
2:  mov ecx, 1
    call update_tray
    add rsp, 40
    ret

check_alive:
    sub rsp, 40
    mov rcx, [rip+pi1]
    xor edx, edx
    call WaitForSingleObject
    test eax, eax
    jz 1f
    mov rcx, [rip+pi2]
    xor edx, edx
    call WaitForSingleObject
    test eax, eax
    jnz 2f
1:  call stop_all
    mov ecx, 1
    call update_tray
2:  add rsp, 40
    ret

# rcx = haystack, rdx = needle (both NUL-terminated) -> rax = pointer just past the first match, or 0
find_str:
    push rsi
    push rdi
.f_outer:
    cmp byte ptr [rcx], 0
    je .f_no
    mov rsi, rcx
    mov rdi, rdx
.f_in:
    mov al, [rdi]
    test al, al
    jz .f_yes
    cmp al, [rsi]
    jne .f_adv
    inc rsi
    inc rdi
    jmp .f_in
.f_adv:
    inc rcx
    jmp .f_outer
.f_yes:
    mov rax, rsi
    pop rdi
    pop rsi
    ret
.f_no:
    xor eax, eax
    pop rdi
    pop rsi
    ret

# rsi = JSON string content (just after the opening quote). Decodes it into u8 as NUL-terminated
# UTF-8 and returns rax = length. Handles \" \\ \/ \n \r \t \b \f and \uXXXX (BMP). Leaves rdi alone.
decode_text:
    lea r10, [rip+u8]
    lea r11, [rip+u8+4090]
.d_loop:
    movzx eax, byte ptr [rsi]
    test eax, eax
    jz .d_end
    cmp eax, 34
    je .d_end
    inc rsi
    cmp eax, 92
    jne .d_put
    movzx eax, byte ptr [rsi]
    test eax, eax
    jz .d_end
    inc rsi
    cmp eax, 110
    jne .d_n1
    mov eax, 10
    jmp .d_put
.d_n1:
    cmp eax, 114
    jne .d_n2
    mov eax, 13
    jmp .d_put
.d_n2:
    cmp eax, 116
    jne .d_n3
    mov eax, 9
    jmp .d_put
.d_n3:
    cmp eax, 98
    jne .d_n4
    mov eax, 8
    jmp .d_put
.d_n4:
    cmp eax, 102
    jne .d_n5
    mov eax, 12
    jmp .d_put
.d_n5:
    cmp eax, 117
    jne .d_put                         # \" \\ \/ stand for themselves
    xor edx, edx
    mov ecx, 4
.d_hex:
    movzx eax, byte ptr [rsi]
    test eax, eax
    jz .d_end
    inc rsi
    sub eax, 48
    cmp eax, 9
    jbe .d_hd
    sub eax, 7
    cmp eax, 15
    jbe .d_hd
    sub eax, 32
.d_hd:
    and eax, 15
    shl edx, 4
    or edx, eax
    dec ecx
    jnz .d_hex
    mov eax, edx
    cmp eax, 0x80
    jb .d_put
    cmp r10, r11
    jae .d_end
    cmp eax, 0x800
    jae .d_u3
    mov edx, eax
    shr edx, 6
    or edx, 0xC0
    mov [r10], dl
    inc r10
    and eax, 0x3F
    or eax, 0x80
    jmp .d_put
.d_u3:
    mov edx, eax
    shr edx, 12
    or edx, 0xE0
    mov [r10], dl
    inc r10
    mov edx, eax
    shr edx, 6
    and edx, 0x3F
    or edx, 0x80
    mov [r10], dl
    inc r10
    and eax, 0x3F
    or eax, 0x80
.d_put:
    cmp r10, r11
    jae .d_end
    mov [r10], al
    inc r10
    jmp .d_loop
.d_end:
    mov byte ptr [r10], 0
    lea rax, [rip+u8]
    sub r10, rax
    mov rax, r10
    ret

# Reads the tail of transcripts.jsonl and fills ents[0..nents-1] with the last (up to 5) lines, newest
# first. Each line is NUL-terminated in place inside fbuf.
load_ents:
    push rbx
    push rsi
    push rdi
    push r12
    sub rsp, 72
    mov dword ptr [rip+nents], 0
    lea rcx, [rip+tpath]
    mov edx, 0x80000000                # GENERIC_READ
    mov r8d, 3                         # FILE_SHARE_READ | FILE_SHARE_WRITE (the daemon keeps appending)
    xor r9d, r9d
    mov qword ptr [rsp+32], 3          # OPEN_EXISTING
    mov qword ptr [rsp+40], 0x80       # FILE_ATTRIBUTE_NORMAL
    mov qword ptr [rsp+48], 0
    call CreateFileA
    cmp rax, -1
    je .le_out
    mov rbx, rax
    mov rcx, rbx
    xor edx, edx
    call GetFileSize
    mov r12d, eax
    mov dword ptr [rip+partial], 0
    cmp r12d, 65536
    jbe .le_read
    mov dword ptr [rip+partial], 1
    mov rcx, rbx
    mov edx, r12d
    sub edx, 65536
    xor r8d, r8d
    xor r9d, r9d
    call SetFilePointer
.le_read:
    mov dword ptr [rip+bytesread], 0
    mov rcx, rbx
    lea rdx, [rip+fbuf]
    mov r8d, 65536
    lea r9, [rip+bytesread]
    mov qword ptr [rsp+32], 0
    call ReadFile
    mov rcx, rbx
    call CloseHandle
    lea rsi, [rip+fbuf]
    mov eax, [rip+bytesread]
    lea rdi, [rsi+rax]
    mov byte ptr [rdi], 0
    xor ecx, ecx
.le_next:
    cmp rdi, rsi
    jbe .le_done
    mov al, [rdi-1]
    cmp al, 10
    je .le_trim
    cmp al, 13
    je .le_trim
    jmp .le_find
.le_trim:
    dec rdi
    jmp .le_next
.le_find:
    mov byte ptr [rdi], 0
    mov rdx, rdi
.le_back:
    cmp rdx, rsi
    jbe .le_start
    cmp byte ptr [rdx-1], 10
    je .le_got
    dec rdx
    jmp .le_back
.le_start:
    cmp dword ptr [rip+partial], 0     # first line of a tail read is cut off: drop it
    jne .le_done
.le_got:
    lea rax, [rip+ents]
    mov [rax+rcx*8], rdx
    inc ecx
    mov rdi, rdx
    cmp ecx, 5
    jb .le_next
.le_done:
    mov [rip+nents], ecx
.le_out:
    add rsp, 72
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret

# ecx = entry index -> menu label ("YYYY-MM-DD HH:MM  text...") as UTF-16 in lbl
build_label:
    push rbx
    push rsi
    push rdi
    push r12
    sub rsp, 72
    mov r12d, ecx
    lea rax, [rip+ents]
    mov rbx, [rax+r12*8]
    lea rdi, [rip+l8]
    mov rcx, rbx
    lea rdx, [rip+s_ktime]
    call find_str
    test rax, rax
    jz .bl_text
    mov rsi, rax
    mov ecx, 16
.bl_t:
    mov al, [rsi]
    test al, al
    jz .bl_sp
    cmp al, 34
    je .bl_sp
    mov [rdi], al
    inc rsi
    inc rdi
    dec ecx
    jnz .bl_t
.bl_sp:
    mov word ptr [rdi], 0x2020
    add rdi, 2
.bl_text:
    mov rcx, rbx
    lea rdx, [rip+s_ktext]
    call find_str
    test rax, rax
    jz .bl_fin
    mov rsi, rax
    call decode_text
    lea rsi, [rip+u8]
    xor r9d, r9d                       # 1 = text was cut, add "..."
    mov ecx, eax
    cmp ecx, 44
    jbe .bl_cp
    mov ecx, 44
.bl_bk:
    movzx edx, byte ptr [rsi+rcx]      # do not cut a UTF-8 character in half
    and edx, 0xC0
    cmp edx, 0x80
    jne .bl_cut
    dec ecx
    jmp .bl_bk
.bl_cut:
    mov r9d, 1
.bl_cp:
    test ecx, ecx
    jz .bl_ell
.bl_c1:
    mov al, [rsi]
    cmp al, 32
    jae .bl_ok
    mov al, 32                         # newline/tab -> space
.bl_ok:
    cmp al, 38
    jne .bl_st
    mov al, 43                         # '&' would become a menu mnemonic
.bl_st:
    mov [rdi], al
    inc rsi
    inc rdi
    dec ecx
    jnz .bl_c1
.bl_ell:
    test r9d, r9d
    jz .bl_fin
    mov dword ptr [rdi], 0x2E2E2E
    add rdi, 3
.bl_fin:
    mov byte ptr [rdi], 0
    mov ecx, 65001                     # CP_UTF8
    xor edx, edx
    lea r8, [rip+l8]
    mov r9d, -1
    lea rax, [rip+lbl]
    mov [rsp+32], rax
    mov qword ptr [rsp+40], 250
    call MultiByteToWideChar
    add rsp, 72
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret

# Small "Copied to clipboard" balloon on the tray icon.
balloon:
    push rsi
    push rdi
    sub rsp, 40
    lea rdi, [rip+nid]
    mov dword ptr [rdi], 528
    mov rax, [rip+hwnd]
    mov [rdi+8], rax
    mov dword ptr [rdi+16], 1
    mov dword ptr [rdi+20], 0x10       # NIF_INFO
    mov dword ptr [rdi+500], 0         # NIIF_NONE
    lea rdi, [rip+nid+176]
    lea rsi, [rip+s_copied]
    call cpy
    lea rdi, [rip+nid+436]
    lea rsi, [rip+s_bbname]
    call cpy
    mov ecx, 1
    lea rdx, [rip+nid]
    call Shell_NotifyIconA
    add rsp, 40
    pop rdi
    pop rsi
    ret

# ecx = entry index (from the last load_ents) -> puts that transcript on the clipboard; eax = 1 on success
copy_entry:
    push rbx
    push rsi
    push rdi
    sub rsp, 64
    cmp ecx, dword ptr [rip+nents]
    jae .ce_fail
    lea rax, [rip+ents]
    mov rcx, [rax+rcx*8]
    lea rdx, [rip+s_ktext]
    call find_str
    test rax, rax
    jz .ce_fail
    mov rsi, rax
    call decode_text
    mov ecx, 65001
    xor edx, edx
    lea r8, [rip+u8]
    mov r9d, -1
    lea rax, [rip+wbuf]
    mov [rsp+32], rax
    mov qword ptr [rsp+40], 4200
    call MultiByteToWideChar
    test eax, eax
    jz .ce_fail
    mov ebx, eax
    mov ecx, 2                         # GMEM_MOVEABLE
    mov edx, ebx
    add rdx, rdx
    call GlobalAlloc
    test rax, rax
    jz .ce_fail
    mov [rsp+48], rax
    mov rcx, rax
    call GlobalLock
    test rax, rax
    jz .ce_free
    mov rdi, rax
    lea rsi, [rip+wbuf]
    mov ecx, ebx
    add ecx, ecx
    rep movsb
    mov rcx, [rsp+48]
    call GlobalUnlock
    mov rcx, [rip+hwnd]
    call OpenClipboard
    test eax, eax
    jz .ce_free
    call EmptyClipboard
    mov ecx, 13                        # CF_UNICODETEXT
    mov rdx, [rsp+48]
    call SetClipboardData
    call CloseClipboard
    call balloon
    mov eax, 1
    jmp .ce_out
.ce_free:
    mov rcx, [rsp+48]
    call GlobalFree
.ce_fail:
    xor eax, eax
.ce_out:
    add rsp, 64
    pop rdi
    pop rsi
    pop rbx
    ret

open_dir:
    sub rsp, 56
    xor ecx, ecx
    lea rdx, [rip+s_open]
    lea r8, [rip+dpath]
    xor r9d, r9d
    mov qword ptr [rsp+32], 0
    mov qword ptr [rsp+40], 1          # SW_SHOWNORMAL
    call ShellExecuteA
    add rsp, 56
    ret

# Builds and shows the right-click menu; eax = chosen command id (0 = none)
#   10..14 recent transcripts, 20 open folder, 1 toggle, 2 quit
show_menu:
    push rbx
    push rsi
    push r12
    sub rsp, 80
    call load_ents
    call CreatePopupMenu
    mov rbx, rax
    xor r12d, r12d
.m_loop:
    cmp r12d, dword ptr [rip+nents]
    jge .m_after
    mov ecx, r12d
    call build_label
    mov rcx, rbx
    xor edx, edx
    lea r8d, [r12+10]
    lea r9, [rip+lbl]
    call AppendMenuW
    inc r12d
    jmp .m_loop
.m_after:
    cmp dword ptr [rip+nents], 0
    jne .m_sep
    mov rcx, rbx
    mov edx, 3                         # MF_GRAYED | MF_DISABLED
    xor r8d, r8d
    lea r9, [rip+s_none]
    call AppendMenuA
.m_sep:
    mov rcx, rbx
    mov edx, 0x800                     # MF_SEPARATOR
    xor r8d, r8d
    xor r9d, r9d
    call AppendMenuA
    mov rcx, rbx
    xor edx, edx
    mov r8d, 20
    lea r9, [rip+s_openf]
    call AppendMenuA
    mov rcx, rbx
    xor edx, edx
    mov r8d, 1
    lea r9, [rip+s_toggle]
    call AppendMenuA
    mov rcx, rbx
    xor edx, edx
    mov r8d, 2
    lea r9, [rip+s_quit]
    call AppendMenuA
    lea rcx, [rip+pt]
    call GetCursorPos
    mov rcx, [rip+hwnd]
    call SetForegroundWindow
    mov rcx, rbx
    mov edx, 0x102                     # TPM_RETURNCMD | TPM_RIGHTBUTTON
    mov r8d, [rip+pt]
    mov r9d, [rip+pt+4]
    mov qword ptr [rsp+32], 0
    mov rax, [rip+hwnd]
    mov [rsp+40], rax
    mov qword ptr [rsp+48], 0
    call TrackPopupMenu
    mov r12d, eax
    mov rcx, rbx
    call DestroyMenu
    mov rcx, [rip+hwnd]
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    call PostMessageA
    mov eax, r12d
    add rsp, 80
    pop r12
    pop rsi
    pop rbx
    ret

wndproc:
    push rbx
    sub rsp, 64
    cmp edx, WM_TRAY
    je w_tray
    cmp edx, WM_TIMER
    je w_timer
    cmp edx, WM_DESTROY
    je w_destroy
    call DefWindowProcA
w_out:
    add rsp, 64
    pop rbx
    ret
w_tray:
    cmp r9d, WM_LBUP
    je w_toggle
    cmp r9d, WM_RBUP
    je w_menu
    cmp r9d, WM_MBUP
    je w_mid
    jmp w_zero
w_mid:
    call load_ents
    xor ecx, ecx
    call copy_entry
    jmp w_zero
w_toggle:
    call toggle
    jmp w_zero
w_timer:
    cmp dword ptr [rip+state], 0
    je w_zero
    call check_alive
w_zero:
    xor eax, eax
    jmp w_out
w_destroy:
    call stop_all
    mov ecx, 2
    call update_tray
    xor ecx, ecx
    call PostQuitMessage
    jmp w_zero
w_menu:
    call show_menu
    cmp eax, 1
    je w_m_toggle
    cmp eax, 2
    je w_m_quit
    cmp eax, 20
    je w_m_open
    cmp eax, 10
    jb w_zero
    cmp eax, 14
    ja w_zero
    sub eax, 10
    mov ecx, eax
    call copy_entry
    jmp w_zero
w_m_toggle:
    call toggle
    jmp w_zero
w_m_quit:
    mov rcx, [rip+hwnd]
    call DestroyWindow
    jmp w_zero
w_m_open:
    call open_dir
    jmp w_zero

start:
    sub rsp, 120
    xor ecx, ecx
    xor edx, edx
    lea r8, [rip+mutex_name]
    call CreateMutexA
    call GetLastError
    cmp eax, 183                       # ERROR_ALREADY_EXISTS
    je exit

    xor ecx, ecx                       # job object: kill children when this process goes away
    xor edx, edx
    call CreateJobObjectA
    mov [rip+hjob], rax
    lea rax, [rip+jbuf]
    mov dword ptr [rax+16], 0x2000     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    mov rcx, [rip+hjob]
    mov edx, 9                         # JobObjectExtendedLimitInformation
    lea r8, [rip+jbuf]
    mov r9d, 144
    call SetInformationJobObject

    lea rcx, [rip+s_up]                # %USERPROFILE%\.bluebox and its transcripts.jsonl
    lea rdx, [rip+home]
    mov r8d, 260
    call GetEnvironmentVariableA
    lea rdi, [rip+dpath]
    lea rsi, [rip+home]
    call cpy
    lea rsi, [rip+s_bbdir]
    call cpy
    lea rdi, [rip+tpath]
    lea rsi, [rip+dpath]
    call cpy
    lea rsi, [rip+s_tj]
    call cpy

    xor ecx, ecx
    lea rdx, [rip+exepath]
    mov r8d, 260
    call GetModuleFileNameA
    lea rsi, [rip+exepath]
1:  cmp byte ptr [rsi], 0
    je 2f
    inc rsi
    jmp 1b
2:  mov ecx, 2                         # drop "\tools\bbtoggle.exe"
3:  dec rsi
    cmp byte ptr [rsi], 92
    jne 3b
    dec ecx
    jnz 3b
    mov byte ptr [rsi], 0

    mov ecx, 0xFF808080
    call make_icon
    mov [rip+hicon_off], rax
    mov ecx, 0xFF2EB82E
    call make_icon
    mov [rip+hicon_on], rax

    xor ecx, ecx
    call GetModuleHandleA
    mov [rip+hinst], rax
    lea rdi, [rip+wc]
    mov dword ptr [rdi], 80
    lea rax, [rip+wndproc]
    mov [rdi+8], rax
    mov rax, [rip+hinst]
    mov [rdi+24], rax
    lea rax, [rip+cls_name]
    mov [rdi+64], rax
    mov rcx, rdi
    call RegisterClassExA

    xor ecx, ecx
    lea rdx, [rip+cls_name]
    lea r8, [rip+cls_name]
    xor r9d, r9d
    mov qword ptr [rsp+32], 0
    mov qword ptr [rsp+40], 0
    mov qword ptr [rsp+48], 0
    mov qword ptr [rsp+56], 0
    mov qword ptr [rsp+64], 0
    mov qword ptr [rsp+72], 0
    mov rax, [rip+hinst]
    mov [rsp+80], rax
    mov qword ptr [rsp+88], 0
    call CreateWindowExA
    mov [rip+hwnd], rax

    xor ecx, ecx                       # NIM_ADD
    call update_tray
    mov rcx, [rip+hwnd]
    mov edx, 1
    mov r8d, 2000
    xor r9d, r9d
    call SetTimer

msgloop:
    lea rcx, [rip+msg]
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    call GetMessageA
    test eax, eax
    jle exit
    lea rcx, [rip+msg]
    call TranslateMessage
    lea rcx, [rip+msg]
    call DispatchMessageA
    jmp msgloop
exit:
    xor ecx, ecx
    call ExitProcess
