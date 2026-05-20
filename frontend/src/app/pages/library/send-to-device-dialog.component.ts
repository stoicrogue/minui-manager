import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  MAT_DIALOG_DATA,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';

import { LibraryGame } from '../../services/library.service';
import {
  SDCardService,
  SlotCapConflict,
  SyncOp,
  SyncPlan,
  SyncResponse,
} from '../../services/sdcard.service';

export interface SendToDeviceData {
  games: LibraryGame[];
}

type Phase =
  | { kind: 'planning' }
  | { kind: 'preview'; plan: SyncPlan }
  | { kind: 'conflict'; conflict: SlotCapConflict }
  | { kind: 'syncing'; plan: SyncPlan }
  | { kind: 'done'; response: SyncResponse }
  | { kind: 'error'; message: string };

@Component({
  selector: 'app-send-to-device-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatDividerModule,
    MatTooltipModule,
  ],
  templateUrl: './send-to-device-dialog.component.html',
  styleUrl: './send-to-device-dialog.component.scss',
})
export class SendToDeviceDialogComponent {
  private readonly api = inject(SDCardService);
  private readonly dialogRef = inject(MatDialogRef<SendToDeviceDialogComponent>);
  readonly data = inject<SendToDeviceData>(MAT_DIALOG_DATA);

  readonly phase = signal<Phase>({ kind: 'planning' });

  readonly libraryIds = this.data.games.map((g) => g.id);

  // Convenience accessors for the template.
  readonly plan = computed(() => {
    const p = this.phase();
    return p.kind === 'preview' || p.kind === 'syncing' ? p.plan : null;
  });
  readonly conflict = computed(() => {
    const p = this.phase();
    return p.kind === 'conflict' ? p.conflict : null;
  });
  readonly response = computed(() => {
    const p = this.phase();
    return p.kind === 'done' ? p.response : null;
  });
  readonly errorMessage = computed(() => {
    const p = this.phase();
    return p.kind === 'error' ? p.message : null;
  });

  constructor() {
    this.runDryRun();
  }

  private runDryRun(): void {
    this.phase.set({ kind: 'planning' });
    this.api.sync(this.libraryIds, true).subscribe({
      next: (r) => this.phase.set({ kind: 'preview', plan: r.plan }),
      error: (err: HttpErrorResponse) => {
        if (err.status === 409 && err.error?.code === 'slot_cap_exceeded') {
          this.phase.set({ kind: 'conflict', conflict: err.error as SlotCapConflict });
        } else {
          this.phase.set({
            kind: 'error',
            message: err.error?.detail ?? err.message ?? 'Planning failed.',
          });
        }
      },
    });
  }

  confirm(): void {
    const current = this.phase();
    if (current.kind !== 'preview') return;
    this.phase.set({ kind: 'syncing', plan: current.plan });
    this.api.sync(this.libraryIds, false).subscribe({
      next: (r) => this.phase.set({ kind: 'done', response: r }),
      error: (err: HttpErrorResponse) => {
        this.phase.set({
          kind: 'error',
          message: err.error?.detail ?? err.message ?? 'Sync failed.',
        });
      },
    });
  }

  close(): void {
    const done = this.response();
    this.dialogRef.close({
      ok: done ? done.result?.ok_count ?? 0 : 0,
      errors: done ? done.result?.error_count ?? 0 : 0,
      cancelled: !done,
    });
  }

  retry(): void {
    this.runDryRun();
  }

  /** Friendly label per op for the preview list. */
  opLabel(op: SyncOp): string {
    switch (op.action) {
      case 'mkdir':
        return `Create folder ${op.dest_rel}`;
      case 'copy':
        return `Copy → ${op.dest_rel}`;
      case 'write_text':
        return `Write ${op.dest_rel}`;
      case 'remove_tree':
        return `Overwrite (remove existing) ${op.dest_rel}`;
    }
  }

  prettySize(bytes: number | null | undefined): string {
    if (bytes == null) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }
}
