from typing import Dict, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from labml_db import Model, Index
from labml_db.serializer.pickle import PickleSerializer
from labml_db.serializer.yaml import YamlSerializer

from labml_app.logger import logger
from labml_app.enums import SeriesEnums
from labml_app.settings import INDICATOR_LIMIT
from ..analysis import Analysis
from ..series import SeriesModel, Series
from ..series_collection import SeriesCollection
from ..preferences import Preferences
from .. import helper


@Analysis.db_model(PickleSerializer, 'parameters')
class ParametersModel(Model['ParametersModel'], SeriesCollection):
    pass


@Analysis.db_model(PickleSerializer, 'parameters_preferences')
class ParametersPreferencesModel(Model['ParametersPreferencesModel'], Preferences):
    pass


@Analysis.db_index(YamlSerializer, 'parameters_preferences_index.yaml')
class ParametersPreferencesIndex(Index['ParametersPreferences']):
    pass


@Analysis.db_index(YamlSerializer, 'parameters_index.yaml')
class ParametersIndex(Index['Parameters']):
    pass


class ParametersAnalysis(Analysis):
    parameters: ParametersModel

    def __init__(self, data):
        self.parameters = data

    def track(self, data: Dict[str, SeriesModel]):
        res = {}
        for ind, s in data.items():
            ind_split = ind.split('.')
            ind_type = ind_split[0]
            ind_name = '.'.join(ind_split[:-1])
            if ind_type == SeriesEnums.PARAM:
                if ind_name not in self.parameters.indicators:
                    if len(self.parameters.indicators) >= INDICATOR_LIMIT:
                        continue
                    self.parameters.indicators.add(ind_name)
                res[ind] = s

        self.parameters.track(res)

    def get_track_summaries(self):
        data = {}
        for ind, track in self.parameters.tracking.items():
            name_split = ind.split('.')
            ind = name_split[-1]
            name = '.'.join(name_split[1:-1])

            series: Dict[str, Any] = Series().load(track).summary

            if name in data:
                data[name][ind] = series['mean']
            else:
                data[name] = {ind: series['mean']}

        if not data:
            return []

        sort_key = 'l2'

        res = [v for k, v in data.items() if sort_key in v]
        sorted_res = sorted(res, key=lambda k: k[sort_key])

        ret = {}
        for d in sorted_res:
            for k, v in d.items():
                if k not in ret:
                    ret[k] = {'name': k, 'value': []}
                else:
                    ret[k]['value'].append(v)

        return [v for k, v in ret.items()]

    def get_tracking(self):
        res = []
        is_series_updated = False
        for ind, track in self.parameters.tracking.items():
            name = ind.split('.')
            if name[-1] != 'l2':
                continue
            name = name[1:-1]

            s = Series().load(track)
            series: Dict[str, Any] = s.detail
            series['name'] = '.'.join(name)

            if s.is_smoothed_updated:
                self.parameters.tracking[ind] = s.to_data()
                is_series_updated = True

            res.append(series)

        if is_series_updated:
            self.parameters.save()

        res.sort(key=lambda s: s['mean'], reverse=True)

        helper.remove_common_prefix(res, 'name')

        return res

    @staticmethod
    def get_or_create(run_uuid: str):
        parameters_key = ParametersIndex.get(run_uuid)

        if not parameters_key:
            p = ParametersModel()
            p.save()
            ParametersIndex.set(run_uuid, p.key)

            pp = ParametersPreferencesModel()
            pp.save()
            ParametersPreferencesIndex.set(run_uuid, pp.key)

            return ParametersAnalysis(p)

        return ParametersAnalysis(parameters_key.load())

    @staticmethod
    def delete(run_uuid: str):
        parameters_key = ParametersIndex.get(run_uuid)
        preferences_key = ParametersPreferencesIndex.get(run_uuid)

        if parameters_key:
            p: ParametersModel = parameters_key.load()
            ParametersIndex.delete(run_uuid)
            p.delete()

        if preferences_key:
            pp: ParametersPreferencesModel = preferences_key.load()
            ParametersPreferencesIndex.delete(run_uuid)
            pp.delete()


@Analysis.route('GET', 'parameters/{run_uuid}')
async def get_params_tracking(request: Request, run_uuid: str) -> Any:
    track_data = []
    summary_data = []
    status_code = 404

    ans = ParametersAnalysis.get_or_create(run_uuid)
    if ans:
        track_data = ans.get_tracking()
        summary_data = ans.get_track_summaries()
        status_code = 200

    response = JSONResponse({'series': track_data, 'insights': [], 'summary': summary_data})
    response.status_code = status_code

    return response


@Analysis.route('GET', 'parameters/preferences/{run_uuid}')
async def get_params_preferences(request: Request, run_uuid: str) -> Any:
    preferences_data = {}

    preferences_key = ParametersPreferencesIndex.get(run_uuid)
    if not preferences_key:
        return preferences_data

    pp: ParametersPreferencesModel = preferences_key.load()

    return pp.get_data()


@Analysis.route('POST', 'parameters/preferences/{run_uuid}')
async def set_params_preferences(request: Request, run_uuid: str) -> Any:
    preferences_key = ParametersPreferencesIndex.get(run_uuid)

    if not preferences_key:
        return {}

    pp = preferences_key.load()
    json = await request.json()
    pp.update_preferences(json)

    logger.debug(f'update parameters preferences: {pp.key}')

    return {'errors': pp.errors}
