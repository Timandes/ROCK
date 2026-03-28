/**
 * Tests for Request types
 * 
 * Tests UploadMode enum and UploadRequest schema validation
 */

import {
  UploadRequestSchema,
} from './requests.js';

describe('UploadMode', () => {
  test('should accept "auto" as valid upload mode', () => {
    const result = UploadRequestSchema.parse({
      sourcePath: '/local/file.txt',
      targetPath: '/remote/file.txt',
      uploadMode: 'auto',
    });
    expect(result.uploadMode).toBe('auto');
  });

  test('should accept "direct" as valid upload mode', () => {
    const result = UploadRequestSchema.parse({
      sourcePath: '/local/file.txt',
      targetPath: '/remote/file.txt',
      uploadMode: 'direct',
    });
    expect(result.uploadMode).toBe('direct');
  });

  test('should accept "oss" as valid upload mode', () => {
    const result = UploadRequestSchema.parse({
      sourcePath: '/local/file.txt',
      targetPath: '/remote/file.txt',
      uploadMode: 'oss',
    });
    expect(result.uploadMode).toBe('oss');
  });

  test('should reject invalid upload mode', () => {
    expect(() => {
      UploadRequestSchema.parse({
        sourcePath: '/local/file.txt',
        targetPath: '/remote/file.txt',
        uploadMode: 'invalid',
      });
    }).toThrow();
  });
});

describe('UploadRequest', () => {
  test('should allow uploadMode to be undefined when not provided', () => {
    const result = UploadRequestSchema.parse({
      sourcePath: '/local/file.txt',
      targetPath: '/remote/file.txt',
    });
    expect(result.uploadMode).toBeUndefined();
  });

  test('should parse valid request with all fields', () => {
    const result = UploadRequestSchema.parse({
      sourcePath: '/local/file.txt',
      targetPath: '/remote/file.txt',
      uploadMode: 'oss',
    });

    expect(result.sourcePath).toBe('/local/file.txt');
    expect(result.targetPath).toBe('/remote/file.txt');
    expect(result.uploadMode).toBe('oss');
  });

  test('should parse minimal request without uploadMode', () => {
    const result = UploadRequestSchema.parse({
      sourcePath: '/local/file.txt',
      targetPath: '/remote/file.txt',
    });

    expect(result.sourcePath).toBe('/local/file.txt');
    expect(result.targetPath).toBe('/remote/file.txt');
    expect(result.uploadMode).toBeUndefined();
  });
});
