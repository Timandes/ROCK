/**
 * Tests for Sandbox Client - Exception handling
 */

import axios from 'axios';
import { Sandbox } from './client.js';
import {
  BadRequestRockError,
  InternalServerRockError,
  CommandRockError,
  RockException,
} from '../common/exceptions.js';
import { Codes } from '../types/codes.js';

// Mock axios
jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

// Helper to create mock axios response
function createMockPost(data: unknown, headers: Record<string, string> = {}) {
  return jest.fn().mockResolvedValue({
    data,
    headers,
  });
}

// Helper to create mock axios get
function createMockGet(data: unknown, headers: Record<string, string> = {}) {
  return jest.fn().mockResolvedValue({
    data,
    headers,
  });
}

describe('Sandbox Exception Handling', () => {
  let sandbox: Sandbox;
  let mockPost: jest.Mock;
  let mockGet: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockPost = jest.fn();
    mockGet = jest.fn();
    mockedAxios.create = jest.fn().mockReturnValue({
      post: mockPost,
      get: mockGet,
    });

    sandbox = new Sandbox({
      image: 'test:latest',
      startupTimeout: 2, // Short timeout for tests
    });
  });

  describe('start() - error code handling', () => {
    test('should throw BadRequestRockError when API returns 4xxx code', async () => {
      // Mock the start_async API to return an error response with code
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Failed',
          result: {
            sandbox_id: 'test-id',
            code: Codes.BAD_REQUEST,
          },
        },
        headers: {},
      });

      await expect(sandbox.start()).rejects.toThrow(BadRequestRockError);
    });

    test('should throw InternalServerRockError when API returns 5xxx code', async () => {
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Failed',
          result: {
            sandbox_id: 'test-id',
            code: Codes.INTERNAL_SERVER_ERROR,
          },
        },
        headers: {},
      });

      await expect(sandbox.start()).rejects.toThrow(InternalServerRockError);
    });

    test('should throw CommandRockError when API returns 6xxx code', async () => {
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Failed',
          result: {
            sandbox_id: 'test-id',
            code: Codes.COMMAND_ERROR,
          },
        },
        headers: {},
      });

      await expect(sandbox.start()).rejects.toThrow(CommandRockError);
    });

    test('should throw RockException for unknown error codes', async () => {
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Failed',
          result: {
            sandbox_id: 'test-id',
            code: 7000,
          },
        },
        headers: {},
      });

      await expect(sandbox.start()).rejects.toThrow(RockException);
    });

    test('should throw InternalServerRockError on startup timeout', async () => {
      // Mock successful start_async but sandbox never becomes alive
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Success',
          result: {
            sandbox_id: 'test-id',
            host_name: 'test-host',
            host_ip: '127.0.0.1',
          },
        },
        headers: {},
      });

      // Mock getStatus to return not alive
      mockGet.mockResolvedValue({
        data: {
          status: 'Success',
          result: {
            is_alive: false,
          },
        },
        headers: {},
      });

      await expect(sandbox.start()).rejects.toThrow(InternalServerRockError);
    }, 10000); // 10s timeout for this test
  });

  describe('execute() - error code handling', () => {
    test('should throw BadRequestRockError when API returns 4xxx code', async () => {
      // First start the sandbox
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Success',
          result: {
            sandbox_id: 'test-id',
            host_name: 'test-host',
            host_ip: '127.0.0.1',
          },
        },
        headers: {},
      });
      mockGet.mockResolvedValue({
        data: {
          status: 'Success',
          result: { is_alive: true },
        },
        headers: {},
      });
      await sandbox.start();

      // Mock execute to return error
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Failed',
          result: {
            code: Codes.BAD_REQUEST,
          },
        },
        headers: {},
      });

      await expect(sandbox.execute({ command: 'test', timeout: 60 })).rejects.toThrow(BadRequestRockError);
    });
  });

  describe('createSession() - error code handling', () => {
    test('should throw BadRequestRockError when API returns 4xxx code', async () => {
      // First start the sandbox
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Success',
          result: {
            sandbox_id: 'test-id',
            host_name: 'test-host',
            host_ip: '127.0.0.1',
          },
        },
        headers: {},
      });
      mockGet.mockResolvedValue({
        data: {
          status: 'Success',
          result: { is_alive: true },
        },
        headers: {},
      });
      await sandbox.start();

      // Mock createSession to return error
      mockPost.mockResolvedValueOnce({
        data: {
          status: 'Failed',
          result: {
            code: Codes.BAD_REQUEST,
          },
        },
        headers: {},
      });

      await expect(sandbox.createSession({ 
        session: 'test', 
        startupSource: [], 
        envEnable: false
      })).rejects.toThrow(BadRequestRockError);
    });
  });
});