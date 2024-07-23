import datetime
import os
from typing import Any, Optional, Dict, List, Set
from sqlmodel import Session, SQLModel, create_engine, select
import ell.store
import cattrs
import numpy as np
from ell.types import SerializedLMP, Invocation, SerializedLMPUses, SerializedLStr
from ell.lstr import lstr
from sqlalchemy import or_, func, and_

class SQLStore(ell.store.Store):
    def __init__(self, db_uri: str):
        self.engine = create_engine(db_uri)
        SQLModel.metadata.create_all(self.engine)

        self.open_files: Dict[str, Dict[str, Any]] = {}


    def write_lmp(self, lmp_id: str, name: str, source: str, dependencies: List[str], is_lmp: bool, lm_kwargs: str, 
                  uses: Dict[str, Any], 
                  created_at: Optional[float]=None) -> Optional[Any]:
        with Session(self.engine) as session:
            lmp = session.query(SerializedLMP).filter(SerializedLMP.lmp_id == lmp_id).first()
            
            if lmp:
                # Already added to the DB.
                return lmp
            else:
                lmp = SerializedLMP(
                    lmp_id=lmp_id,
                    name=name,
                    source=source,
                    dependencies=dependencies,
                    created_at=datetime.datetime.fromtimestamp(created_at) if created_at else datetime.datetime.utcnow(),
                    is_lm=is_lmp,
                    lm_kwargs=lm_kwargs
                )
                session.add(lmp)
            
            for use_id in uses:
                used_lmp = session.exec(select(SerializedLMP).where(SerializedLMP.lmp_id == use_id)).first()
                if used_lmp:
                    lmp.uses.append(used_lmp)
            
            session.commit()
        return None

    def write_invocation(self, lmp_id: str, args: str, kwargs: str, result: lstr | List[lstr], invocation_kwargs: Dict[str, Any], consumes: Set[str],
                         created_at: Optional[float] = None) -> Optional[Any]:
        with Session(self.engine) as session:
            if isinstance(result, lstr):
                results = [result]
            elif isinstance(result, list):
                results = result
            else:
                raise TypeError("Result must be either lstr or List[lstr]")

            lmp = session.query(SerializedLMP).filter(SerializedLMP.lmp_id == lmp_id).first()
            assert lmp is not None, f"LMP with id {lmp_id} not found. Writing invocation erroneously"
            
            invocation = Invocation(
                lmp_id=lmp.lmp_id,
                args=args,
                kwargs=kwargs,
                created_at=created_at,
                invocation_kwargs=str(invocation_kwargs)
            )

            for res in results:
                serialized_lstr = SerializedLStr(content=str(res), logits=res.logits)
                serialized_lstr.originator.append(lmp)
                session.add(serialized_lstr)
                invocation.results.append(serialized_lstr)
            

            session.add(invocation)
            session.commit()
        

    def get_lmps(self, **filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with Session(self.engine) as session:
            query = select(SerializedLMP, SerializedLMPUses.lmp_using_id).outerjoin(
                SerializedLMPUses,
                SerializedLMP.lmp_id == SerializedLMPUses.lmp_user_id
            )
            
            if filters:
                for key, value in filters.items():
                    query = query.where(getattr(SerializedLMP, key) == value)
            results = session.exec(query).all()
            
            lmp_dict = {lmp.lmp_id: {**lmp.model_dump(), 'uses': []} for lmp, _ in results}
            for lmp, using_id in results:
                if using_id:
                    lmp_dict[lmp.lmp_id]['uses'].append(using_id)
            return list(lmp_dict.values())

    def get_invocations(self, lmp_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with Session(self.engine) as session:
            query = select(Invocation).where(Invocation.lmp_id == lmp_id)
            if filters:
                for key, value in filters.items():
                    query = query.where(getattr(Invocation, key) == value)
            invocations = session.exec(query).all()
            return [inv for inv in invocations]


    def get_lmp_versions(self, lmp_id: str) -> List[Dict[str, Any]]:
        return self.get_lmps(lmp_id=lmp_id)

    def get_latest_lmps(self) -> List[Dict[str, Any]]:
        raise NotImplementedError()


class SQLiteStore(SQLStore):
    def __init__(self, storage_dir: str):
        os.makedirs(storage_dir, exist_ok=True)
        db_path = os.path.join(storage_dir, 'ell.db')
        super().__init__(f'sqlite:///{db_path}')