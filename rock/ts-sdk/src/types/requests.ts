/**
 * Request types
 */

import { z } from 'zod';

/**
 * Command execution request
 */
export const CommandSchema = z.object({
  command: z.union([z.string(), z.array(z.string())]),
  timeout: z.number().optional().default(1200),
  env: z.record(z.string()).optional(),
  cwd: z.string().optional(),
});

export type Command = z.infer<typeof CommandSchema>;

/**
 * Bash session creation request
 */
export const CreateBashSessionRequestSchema = z.object({
  session: z.string().default('default'),
  startupSource: z.array(z.string()).default([]),
  envEnable: z.boolean().default(false),
  env: z.record(z.string()).optional(),
  remoteUser: z.string().optional(),
});

export type CreateBashSessionRequest = z.infer<typeof CreateBashSessionRequestSchema>;

/**
 * Bash action for session execution
 */
export const BashActionSchema = z.object({
  command: z.string(),
  session: z.string().default('default'),
  timeout: z.number().optional(),
  check: z.enum(['silent', 'raise', 'ignore']).default('raise'),
});

export type BashAction = z.infer<typeof BashActionSchema>;

/**
 * Write file request
 */
export const WriteFileRequestSchema = z.object({
  content: z.string(),
  path: z.string(),
});

export type WriteFileRequest = z.infer<typeof WriteFileRequestSchema>;

/**
 * Read file request
 */
export const ReadFileRequestSchema = z.object({
  path: z.string(),
  encoding: z.string().optional(),
  errors: z.string().optional(),
});

export type ReadFileRequest = z.infer<typeof ReadFileRequestSchema>;

/**
 * Upload mode enum
 * - auto: Automatically choose upload method based on file size and OSS availability
 * - direct: Force direct HTTP upload
 * - oss: Force OSS upload
 */
export const UploadModeSchema = z.enum(['auto', 'direct', 'oss']);

export type UploadMode = z.infer<typeof UploadModeSchema>;

/**
 * Download mode enum
 * - auto: Automatically choose download method based on file size and OSS availability
 * - direct: Force direct HTTP download via readFile API
 * - oss: Force OSS download
 */
export const DownloadModeSchema = z.enum(['auto', 'direct', 'oss']);

export type DownloadMode = z.infer<typeof DownloadModeSchema>;

/**
 * Upload file request
 * Note: uploadMode defaults to 'auto' in the implementation, not in the schema
 */
export const UploadRequestSchema = z.object({
  sourcePath: z.string(),
  targetPath: z.string(),
  uploadMode: UploadModeSchema.optional(),
});

export type UploadRequest = z.infer<typeof UploadRequestSchema>;

/**
 * Close session request
 */
export const CloseSessionRequestSchema = z.object({
  session: z.string().default('default'),
});

export type CloseSessionRequest = z.infer<typeof CloseSessionRequestSchema>;

/**
 * Chown request
 */
export const ChownRequestSchema = z.object({
  remoteUser: z.string(),
  paths: z.array(z.string()).default([]),
  recursive: z.boolean().default(false),
});

export type ChownRequest = z.infer<typeof ChownRequestSchema>;

/**
 * Chmod request
 */
export const ChmodRequestSchema = z.object({
  paths: z.array(z.string()).default([]),
  mode: z.string().default('755'),
  recursive: z.boolean().default(false),
});

export type ChmodRequest = z.infer<typeof ChmodRequestSchema>;

/**
 * Progress phase for upload operations
 * - upload-to-oss: Uploading from local to OSS
 * - download-to-sandbox: Downloading from OSS to sandbox (via wget)
 */
export type UploadPhase = 'upload-to-oss' | 'download-to-sandbox';

/**
 * Progress phase for download operations
 * - upload-to-oss-from-sandbox: Uploading from sandbox to OSS (via ossutil)
 * - download-to-local: Downloading from OSS to local
 */
export type DownloadPhase = 'upload-to-oss-from-sandbox' | 'download-to-local';

/**
 * Progress information callback
 * @param phase - Current phase of the transfer
 * @param percent - Progress percentage (0-100), or -1 if not available
 */
export interface ProgressInfo {
  phase: UploadPhase | DownloadPhase;
  percent: number;
}

/**
 * Upload options
 */
export interface UploadOptions {
  uploadMode?: UploadMode;
  timeout?: number;
  onProgress?: (info: ProgressInfo) => void;
}

/**
 * Download options
 */
export interface DownloadOptions {
  downloadMode?: DownloadMode;
  timeout?: number;
  onProgress?: (info: ProgressInfo) => void;
}
