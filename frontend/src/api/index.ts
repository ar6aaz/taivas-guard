import { httpApi } from './http';
import { mockApi } from './mock';
import type { Api } from './types';

// Live services by default; `npm run dev:sample` serves sample data with no backend.
export const api: Api = import.meta.env.VITE_API_MODE === 'sample' ? mockApi : httpApi;
