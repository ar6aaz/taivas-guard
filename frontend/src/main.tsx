import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Overview } from './pages/Overview';
import { Analyze } from './pages/Analyze';
import { Runs } from './pages/Runs';
import { RunDetail } from './pages/RunDetail';
import { Findings } from './pages/Findings';
import { FindingDetail } from './pages/FindingDetail';
import { Reference } from './pages/Reference';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="analyze" element={<Analyze />} />
          <Route path="runs" element={<Runs />} />
          <Route path="runs/:id" element={<RunDetail />} />
          <Route path="findings" element={<Findings />} />
          <Route path="findings/:id" element={<FindingDetail />} />
          <Route path="reference" element={<Reference />} />
          <Route path="*" element={<div className="empty"><h1>Page not found</h1></div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
