# bbtail: show the last transcripts from bluebox's log, in x86-64 Linux assembly.
#
#   bbtail [N] [FILE]     N lines (default 10, 0 = all); FILE defaults to ~/.bluebox/transcripts.jsonl
#
# Each log line is JSON: {"time": "...", "audio": "...", "text": "..."[, "error": "..."]}.
# This prints "TIME  text" and appends " [error]" for failed transcriptions. It does not parse
# JSON in general: it finds the "time" and "text" keys and unescapes \" \\ \n \t in the text.
#
# Build (GNU binutils, no libc):
#   as --64 -o bbtail.o bbtail.s && ld -o bbtail bbtail.o
#
# Linux only: it talks to the kernel directly (open, read, write, lseek, exit).

.intel_syntax noprefix

.set BUFSZ, 4194304            # only the newest 4 MiB of the log is read
.set OUTSZ, 65536
.set SYS_read, 0
.set SYS_write, 1
.set SYS_open, 2
.set SYS_lseek, 8
.set SYS_exit, 60

.section .rodata
dflt:       .asciz "/.bluebox/transcripts.jsonl"
home_key:   .asciz "HOME="
k_time:     .asciz "\"time\": \""
k_text:     .asciz "\"text\": \""
k_err:      .asciz "\"error\""
err_tag:    .asciz " [error]"
msg_open:   .asciz "bbtail: cannot open the log file\n"
msg_nohome: .asciz "bbtail: HOME is not set; pass the file path\n"

.section .bss
.lcomm buf, BUFSZ + 1
.lcomm out, OUTSZ
.lcomm path, 4096

.text
.globl _start

# ---- find: rsi = start, rdx = end, rdi = NUL-terminated needle.
#      returns rax = pointer just past the first match, or 0. Clobbers rcx, r8, r9, r10.
find:
    mov r10, rsi
1:  cmp r10, rdx
    jae 8f
    xor ecx, ecx
2:  mov r8b, [rdi + rcx]
    test r8b, r8b
    jz 7f                       # whole needle matched
    lea r9, [r10 + rcx]
    cmp r9, rdx
    jae 8f                      # ran out of haystack
    cmp r8b, [r9]
    jne 3f
    inc rcx
    jmp 2b
3:  inc r10
    jmp 1b
7:  lea rax, [r10 + rcx]
    ret
8:  xor eax, eax
    ret

# ---- emit_record: rsi = line start, rdx = line end. Builds one output line and writes it.
#      Clobbers most registers; the caller keeps its state in rbx, r12-r15, which are saved here.
emit_record:
    push rbx
    push r12
    push r13
    push r10                    # find clobbers r10, the caller's end-of-data pointer
    mov r12, rsi                # line start
    mov r13, rdx                # line end
    lea rbx, [rip + out]        # write cursor

    # time: 19 characters after the key, then two spaces
    mov rsi, r12
    mov rdx, r13
    lea rdi, [rip + k_time]
    call find
    test rax, rax
    jz 20f
    lea rcx, [rax + 19]
    cmp rcx, r13
    ja 20f
    mov rsi, rax
    mov ecx, 19
10: mov al, [rsi]
    mov [rbx], al
    inc rsi
    inc rbx
    dec ecx
    jnz 10b
    mov byte ptr [rbx], 0x20
    mov byte ptr [rbx + 1], 0x20
    add rbx, 2

    # text: copy until the closing quote, undoing the JSON escapes
20: mov rsi, r12
    mov rdx, r13
    lea rdi, [rip + k_text]
    call find
    test rax, rax
    jz 40f
    mov rsi, rax
    lea r8, [rip + out + OUTSZ - 16]   # stop before the buffer end
21: cmp rsi, r13
    jae 40f
    cmp rbx, r8
    jae 40f
    mov al, [rsi]
    inc rsi
    cmp al, 0x22                # closing quote
    je 40f
    cmp al, 0x5C                # backslash: look at the next character
    jne 29f
    cmp rsi, r13
    jae 40f
    mov al, [rsi]
    inc rsi
    cmp al, 0x6E                # \n -> space
    jne 22f
    mov al, 0x20
    jmp 29f
22: cmp al, 0x74                # \t -> space
    jne 29f
    mov al, 0x20
29: mov [rbx], al               # \" and \\ fall through as the character itself
    inc rbx
    jmp 21b

    # a failed transcription carries an "error" key
40: mov rsi, r12
    mov rdx, r13
    lea rdi, [rip + k_err]
    call find
    test rax, rax
    jz 50f
    lea rsi, [rip + err_tag]
41: mov al, [rsi]
    test al, al
    jz 50f
    mov [rbx], al
    inc rsi
    inc rbx
    jmp 41b

50: mov byte ptr [rbx], 0x0A
    inc rbx
    lea rsi, [rip + out]
    mov rdx, rbx
    sub rdx, rsi
    mov edi, 1
    mov eax, SYS_write
    syscall
    pop r10
    pop r13
    pop r12
    pop rbx
    ret

# ---- fail: rsi = message. Prints it to stderr and exits 1.
fail:
    mov rdx, rsi
1:  cmp byte ptr [rdx], 0
    je 2f
    inc rdx
    jmp 1b
2:  sub rdx, rsi
    mov edi, 2
    mov eax, SYS_write
    syscall
    mov edi, 1
    mov eax, SYS_exit
    syscall

_start:
    mov rbx, [rsp]              # argc
    lea r12, [rsp + 8]          # argv
    lea r13, [r12 + rbx*8 + 8]  # envp
    mov r15d, 10                # default: last 10 lines

    # N from argv[1]
    cmp rbx, 2
    jb 5f
    mov rsi, [r12 + 8]
    xor r15d, r15d
3:  movzx eax, byte ptr [rsi]
    sub eax, 0x30
    cmp eax, 9
    ja 5f
    imul r15d, r15d, 10
    add r15d, eax
    inc rsi
    jmp 3b

    # path: argv[2], or $HOME + the default tail
5:  cmp rbx, 3
    jb 6f
    mov rdi, [r12 + 16]
    jmp 9f
6:  mov rsi, r13
7:  mov rdi, [rsi]
    test rdi, rdi
    jz 8f                       # no HOME in the environment
    lea rdx, [rip + home_key]
    xor ecx, ecx
71: mov al, [rdx + rcx]
    test al, al
    jz 74f                      # matched "HOME="
    cmp al, [rdi + rcx]
    jne 72f
    inc rcx
    jmp 71b
72: add rsi, 8
    jmp 7b
74: lea rsi, [rdi + 5]
    lea rdi, [rip + path]
75: mov al, [rsi]
    test al, al
    jz 76f
    mov [rdi], al
    inc rsi
    inc rdi
    jmp 75b
76: lea rsi, [rip + dflt]
77: mov al, [rsi]
    mov [rdi], al
    inc rsi
    inc rdi
    test al, al
    jnz 77b
    lea rdi, [rip + path]
    jmp 9f
8:  lea rsi, [rip + msg_nohome]
    call fail

    # open the file, then read only the newest BUFSZ bytes
9:  xor esi, esi                # O_RDONLY
    mov eax, SYS_open
    syscall
    test rax, rax
    js 91f
    mov r14, rax                # fd
    mov rdi, r14
    xor esi, esi
    mov edx, 2                  # SEEK_END
    mov eax, SYS_lseek
    syscall
    xor r8d, r8d                # offset to start reading from
    cmp rax, BUFSZ
    jbe 92f
    lea r8, [rax - BUFSZ]
92: mov rdi, r14
    mov rsi, r8
    xor edx, edx                # SEEK_SET
    mov eax, SYS_lseek
    syscall
    mov rbp, r8                 # remember whether we started mid-file
    xor r9d, r9d                # bytes read so far
93: mov rdi, r14
    lea rsi, [rip + buf]
    add rsi, r9
    mov rdx, BUFSZ
    sub rdx, r9
    jz 94f
    mov eax, SYS_read
    syscall
    test rax, rax
    jle 94f
    add r9, rax
    jmp 93b
94: mov r8, r9                  # r8 = length of data in buf
    lea r9, [rip + buf]         # r9 = cursor
    lea r10, [r9 + r8]          # r10 = end of data
    test rbp, rbp
    jz 95f
96: cmp r9, r10                 # started mid-file: drop the partial first line
    jae 95f
    cmp byte ptr [r9], 0x0A
    lea r9, [r9 + 1]
    jne 96b

    # count lines so we know how many to skip
95: xor r11d, r11d
    mov rsi, r9
97: cmp rsi, r10
    jae 98f
    cmp byte ptr [rsi], 0x0A
    jne 99f
    inc r11
99: inc rsi
    jmp 97b
98: cmp rsi, r9
    je 100f                     # empty
    cmp byte ptr [rsi - 1], 0x0A
    je 100f
    inc r11                     # last line had no newline
100: xor ebp, ebp               # lines to skip
    test r15d, r15d
    jz 101f
    cmp r11, r15
    jbe 101f
    mov rbp, r11
    sub rbp, r15
101: xor ebx, ebx               # current line index

    # walk the lines and print the ones we keep
102: cmp r9, r10
    jae 110f
    mov rsi, r9
103: cmp rsi, r10
    jae 104f
    cmp byte ptr [rsi], 0x0A
    je 104f
    inc rsi
    jmp 103b
104: mov rdx, rsi               # line end (at the newline or the end of data)
    mov r12, rsi
    cmp rbx, rbp
    jb 105f
    mov rsi, r9
    call emit_record
105: inc rbx
    lea r9, [r12 + 1]
    jmp 102b

110: xor edi, edi
    mov eax, SYS_exit
    syscall

91: lea rsi, [rip + msg_open]
    call fail
