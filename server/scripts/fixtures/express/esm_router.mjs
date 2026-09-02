import { Router } from 'express';

const router = Router();

router.get('/', listItems);
router.post('/', createItem);

function listItems(req, res) {}
function createItem(req, res) {}

export default router;
