import express from 'express';
import itemsRouter from './esm_router';

const app = express();

app.get('/status', (req, res) => {
  res.send('ok');
});
app.use('/api/items', itemsRouter);

export default app;
