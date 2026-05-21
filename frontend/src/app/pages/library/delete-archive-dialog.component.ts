import { Component, inject, signal } from '@angular/core';
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

import { ArchiveService } from '../../services/archive.service';
import { ArchivedGame } from '../../services/sdcard.service';

export interface DeleteArchiveData {
  item: ArchivedGame;
}

@Component({
  selector: 'app-delete-archive-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
  ],
  templateUrl: './delete-archive-dialog.component.html',
  styleUrl: './delete-archive-dialog.component.scss',
})
export class DeleteArchiveDialogComponent {
  private readonly api = inject(ArchiveService);
  private readonly dialogRef = inject(MatDialogRef<DeleteArchiveDialogComponent>);
  readonly data = inject<DeleteArchiveData>(MAT_DIALOG_DATA);

  readonly working = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  confirm(): void {
    this.working.set(true);
    this.errorMessage.set(null);
    this.api.delete(this.data.item.id).subscribe({
      next: (r) => {
        this.dialogRef.close({ deleted: true, archived: r.deleted });
      },
      error: (err: HttpErrorResponse) => {
        this.working.set(false);
        this.errorMessage.set(
          err.error?.detail ?? err.message ?? 'Could not delete this archive entry.',
        );
      },
    });
  }

  cancel(): void {
    this.dialogRef.close({ deleted: false });
  }
}
