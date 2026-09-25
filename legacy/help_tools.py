import time
import pandas as pd
import requests
from datetime import date
import ast
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


######### TMDB
def get_movie_info(api_key, movie_id, get_director=True, timeout=20):
    url = f'https://api.themoviedb.org/3/movie/{movie_id}'
    params = {'api_key': api_key}

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, dict):
        raise ValueError(f'Invalid TMDB response for movie_id={movie_id}: {data}')

    genres = str([s['name'] for s in data.get('genres', [])])

    countries = [s['name'] for s in data.get('production_countries', [])]
    if len(countries) == 1:
        countries = countries[0]
    countries = str(countries)

    runtime = data.get('runtime')
    movie_status = data.get('status')
    imdb_id = data.get('imdb_id')

    if get_director:
        credits_url = f'https://api.themoviedb.org/3/movie/{movie_id}/credits'
        credits_response = requests.get(credits_url, params=params, timeout=timeout)
        credits_response.raise_for_status()
        credits_data = credits_response.json()

        crew = credits_data.get('crew', [])
        directors = [person['name'] for person in crew if person.get('job') == 'Director']
        if len(directors) == 1:
            directors = directors[0]
        directors = str(directors)

        return [movie_id, movie_status, runtime, genres, countries, directors, imdb_id]

    return [movie_id, movie_status, runtime, genres, countries, imdb_id]


def get_one_actor_filmo(api_key, actor_id, actor_name=None, excl_films=None, flt_dict=None, timeout=20):
    if excl_films is None:
        excl_films = []
    if flt_dict is None:
        flt_dict = {}

    credits_url = f'https://api.themoviedb.org/3/person/{actor_id}/movie_credits'

    response = requests.get(
        credits_url,
        params={'api_key': api_key, 'language': flt_dict.get('language')},
        timeout=timeout
    )
    response.raise_for_status()
    data = response.json()

    movies = data.get('cast', [])
    if not isinstance(movies, list):
        raise ValueError(f'Invalid cast response for actor_id={actor_id}: {data}')

    info = {}
    for field in ['title', 'original_title', 'release_date', 'id', 'vote_average', 'vote_count']:
        info[field] = [s.get(field) for s in movies]

    df_stat = pd.DataFrame(info).rename(columns={'id': 'movie_id'})

    if df_stat.empty:
        return pd.DataFrame(columns=[
            'title', 'original_title', 'release_date', 'movie_id',
            'vote_average', 'vote_count',
            'movie_status', 'runtime', 'genres', 'countries', 'directors', 'imdb_id'
        ])

    df_stat['release_date'] = df_stat['release_date'].fillna('')
    df_stat['vote_count'] = pd.to_numeric(df_stat['vote_count'], errors='coerce').fillna(0)
    df_stat['vote_average'] = pd.to_numeric(df_stat['vote_average'], errors='coerce').fillna(0)

    df_stat = df_stat[
        (df_stat.release_date > flt_dict.get('release_date_min', '1900-01-01'))
        & (df_stat.release_date < date.today().strftime('%Y-%m-%d'))
        & (df_stat.vote_count > flt_dict.get('vote_count_min', 0))
        & (df_stat.vote_average > flt_dict.get('vote_average_min', 0))
        & (~df_stat.movie_id.isin(excl_films))
    ].copy()

    df_movie_rows = []
    for movie_id in df_stat.movie_id.values:
        try:
            result = get_movie_info(api_key, movie_id, get_director=True, timeout=timeout)
            df_movie_rows.append(result)
        except Exception as e:
            print(
                f'[TMDB][MOVIE_ERROR][actor={actor_name or actor_id}] '
                f'movie_id={movie_id} error={type(e).__name__}: {e}'
            )

    df_movie = pd.DataFrame(
        df_movie_rows,
        columns=['movie_id', 'movie_status', 'runtime', 'genres', 'countries', 'directors', 'imdb_id']
    )

    return df_stat.merge(df_movie, on='movie_id', how='left')


def get_one_director_filmo(api_key, director_id, director_name=None, excl_films=None, flt_dict=None, timeout=20):
    if excl_films is None:
        excl_films = []
    if flt_dict is None:
        flt_dict = {}

    url = f'https://api.themoviedb.org/3/person/{director_id}/movie_credits'
    params = {'api_key': api_key, 'language': flt_dict.get('language')}

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    crew = data.get('crew', [])
    if not isinstance(crew, list):
        raise ValueError(f'Invalid crew response for director_id={director_id}: {data}')

    data = [m for m in crew if m.get('job') == 'Director']

    info = {}
    for field in ['title', 'original_title', 'release_date', 'id', 'vote_average', 'vote_count']:
        info[field] = [s.get(field) for s in data]

    df_stat = pd.DataFrame(info).rename(columns={'id': 'movie_id'})

    if df_stat.empty:
        return pd.DataFrame(columns=[
            'title', 'original_title', 'release_date', 'movie_id',
            'vote_average', 'vote_count',
            'movie_status', 'runtime', 'genres', 'countries', 'imdb_id'
        ])

    df_stat['release_date'] = df_stat['release_date'].fillna('')
    df_stat['vote_count'] = pd.to_numeric(df_stat['vote_count'], errors='coerce').fillna(0)
    df_stat['vote_average'] = pd.to_numeric(df_stat['vote_average'], errors='coerce').fillna(0)

    df_stat = df_stat[
        (df_stat.release_date > flt_dict.get('release_date_min', '1900-01-01'))
        & (df_stat.release_date < date.today().strftime('%Y-%m-%d'))
        & (df_stat.vote_count > flt_dict.get('vote_count_min', 0))
        & (df_stat.vote_average > flt_dict.get('vote_average_min', 0))
        & (~df_stat.movie_id.isin(excl_films))
    ].copy()

    df_movie_rows = []
    for movie_id in df_stat.movie_id.values:
        try:
            result = get_movie_info(api_key, movie_id, get_director=False, timeout=timeout)
            df_movie_rows.append(result)
        except Exception as e:
            print(
                f'[TMDB][MOVIE_ERROR][director={director_name or director_id}] '
                f'movie_id={movie_id} error={type(e).__name__}: {e}'
            )

    df_movie = pd.DataFrame(
        df_movie_rows,
        columns=['movie_id', 'movie_status', 'runtime', 'genres', 'countries', 'imdb_id']
    )

    return df_stat.merge(df_movie, on='movie_id', how='left')


############
def get_actors_filmo(api_key, actor_dict, excl_films=None, flt_dict=None, timeout=20):
    if excl_films is None:
        excl_films = []
    if flt_dict is None:
        flt_dict = {}

    df_stat = pd.DataFrame(
        None,
        columns=[
            'title', 'original_title', 'release_date', 'movie_id',
            'vote_average', 'vote_count',
            'movie_status', 'runtime', 'genres', 'countries',
            'directors', 'imdb_id', 'person', 'person_type'
        ]
    )

    for actor_name, actor_id in actor_dict.items():
        try:
            tmp = get_one_actor_filmo(
                api_key,
                actor_id,
                actor_name=actor_name,
                excl_films=excl_films,
                flt_dict=flt_dict,
                timeout=timeout
            )
            tmp['person'] = actor_name
            tmp['person_type'] = 'actor'
            df_stat = pd.concat([df_stat, tmp], ignore_index=True)
        except Exception as e:
            print(
                f'[TMDB][PERSON_ERROR][actor={actor_name}] '
                f'actor_id={actor_id} error={type(e).__name__}: {e}'
            )

    return df_stat


def get_directors_filmo(api_key, director_dict, excl_films=None, flt_dict=None, timeout=20):
    if excl_films is None:
        excl_films = []
    if flt_dict is None:
        flt_dict = {}

    df_stat = pd.DataFrame(
        None,
        columns=[
            'title', 'original_title', 'release_date', 'movie_id',
            'vote_average', 'vote_count',
            'movie_status', 'runtime', 'genres', 'countries',
            'imdb_id', 'person', 'person_type'
        ]
    )

    for director_name, director_id in director_dict.items():
        try:
            tmp = get_one_director_filmo(
                api_key,
                director_id,
                director_name=director_name,
                excl_films=excl_films,
                flt_dict=flt_dict,
                timeout=timeout
            )
            tmp['person'] = director_name
            tmp['person_type'] = 'director'
            df_stat = pd.concat([df_stat, tmp], ignore_index=True)
        except Exception as e:
            print(
                f'[TMDB][PERSON_ERROR][director={director_name}] '
                f'director_id={director_id} error={type(e).__name__}: {e}'
            )

    return df_stat


def get_imdb_info(api_key_omdb, df, timeout=20):
    tmp = df.copy()
    awards_info = []
    imdb_rating = []

    for imdb_id in df.imdb_id.values:
        try:
            url = f'http://www.omdbapi.com/?i={imdb_id}&apikey={api_key_omdb}'
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()

            imdb_rating.append(data.get('imdbRating'))
            awards_info.append(data.get('Awards'))
        except Exception as e:
            print(f'[OMDB][ERROR] imdb_id={imdb_id} error={type(e).__name__}: {e}')
            awards_info.append(None)
            imdb_rating.append(None)

    tmp['awards'] = awards_info
    tmp['imdb_rating'] = imdb_rating
    return tmp

########## POST-PROCESSING
def get_filter(df, **args):
    tmp = df.copy()

    # безопасная фильтрация по жанрам
    genres_flt = args.get('genres_flt', [])
    if 'genres' in tmp.columns and len(genres_flt) > 0:
        tmp['genres'] = tmp['genres'].fillna('')
        for g in genres_flt:
            tmp = tmp[~tmp['genres'].apply(lambda x: g in x)]

    # безопасное приведение imdb rating
    def get_imdbr(x):
        try:
            return float(x)
        except Exception:
            return 10.0

    # безопасные числовые поля
    for col in ['vote_count', 'vote_average', 'runtime']:
        if col in tmp.columns:
            tmp[col] = pd.to_numeric(tmp[col], errors='coerce')

    if 'release_date' in tmp.columns:
        tmp['release_date'] = tmp['release_date'].fillna('')

    tmp = tmp[
        (~tmp.movie_id.isin(args.get('excl_films', [])))
        & (tmp.release_date > args.get('release_date_min', '1900-01-01'))
        & (tmp.vote_count > args.get('vote_count_min', 0))
        & (tmp.vote_average > args.get('vote_average_min', 0))
        & (tmp.runtime > args.get('runtime_min', 0))
        & (tmp.imdb_rating.apply(get_imdbr) > args.get('imdb_rating_min', 0))
    ]

    # если каких-то колонок нет, аккуратно создаём пустые
    required_cols = [
        'person', 'person_type', 'title', 'original_title', 'directors',
        'release_date', 'imdb_rating', 'awards', 'genres', 'runtime',
        'imdb_id', 'countries', 'movie_status', 'vote_average',
        'vote_count', 'movie_id'
    ]
    for col in required_cols:
        if col not in tmp.columns:
            tmp[col] = None

    tmp = tmp[required_cols]

    drop_columns = args.get('drop_columns', [])
    drop_columns = [c for c in drop_columns if c in tmp.columns]

    return (
        tmp.sort_values(by=['person', 'release_date'], ascending=False)
           .fillna('')
           .drop(columns=drop_columns)
           .reset_index(drop=True)
    )

########## SPOTIFY
def get_top_n_id_by_name(client_id, client_secret, artist_name='', top_cnt=2):
    client_credentials_manager = SpotifyClientCredentials(
        client_id=client_id,
        client_secret=client_secret
    )
    sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
    result = sp.search(q=f'artist:{artist_name}', type='artist', limit=top_cnt)

    return [
        f"""{s['name']} - {s['id']} - popularity: {s['popularity']}"""
        for s in result['artists']['items']
    ]


def sp_get_updates(sp, artist_id):
    albums = sp.artist_albums(artist_id, album_type='album', limit=50)
    albums_list = albums['items']

    if len(albums_list) == 0:
        return pd.DataFrame(
            columns=['artist', 'album', 'release_date', 'tracks_cnt', 'uri', 'album_artists', 'album_type']
        )

    artist_name = albums_list[0]['artists'][0]['name']
    total_albums_cnt = albums['total']

    offset = 50
    while offset < total_albums_cnt:
        albums_list_ = sp.artist_albums(
            artist_id,
            album_type='album',
            limit=50,
            offset=offset
        )['items']
        albums_list = albums_list + albums_list_
        offset += 50

    i = 0
    df_stat = pd.DataFrame(
        None,
        columns=['artist', 'album', 'release_date', 'tracks_cnt', 'uri', 'album_artists', 'album_type']
    )

    for s in albums_list:
        if s['album_type'] not in ('compilation',):
            df_stat.loc[i, :] = (
                artist_name,
                s['name'],
                s['release_date'],
                s['total_tracks'],
                s['uri'],
                str([j['name'] for j in s['artists']]),
                s['album_type']
            )
            i += 1

    return df_stat


def sp_get_albums_info(client_id, client_secret, artist_list):
    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret
        )
    )

    df_stat = pd.DataFrame(
        None,
        columns=['artist', 'album', 'release_date', 'tracks_cnt', 'uri', 'album_artists', 'album_type']
    )

    for artist_id in artist_list:
        tmp = pd.DataFrame(
            columns=['artist', 'album', 'release_date', 'tracks_cnt', 'uri', 'album_artists', 'album_type']
        )
        try:
            tmp = sp_get_updates(sp, artist_id)
        except Exception as e:
            print(f'[SPOTIFY][ARTIST_ERROR] artist_id={artist_id} error={type(e).__name__}: {e}')

        time.sleep(0.1)
        df_stat = pd.concat([df_stat, tmp], ignore_index=True)

    return df_stat


def sp_get_filter(df_stat,
                  release_date_min='2024-01-01',
                  release_date_max='2030-01-01',
                  album_flt=None,
                  album_type_flt=None,
                  max_artists_in_album=1,
                  other_album_author=False,
                  columns_excl=None):

    if album_flt is None:
        album_flt = ['deluxe', 'live', 'edition', 'soundtrack']
    if album_type_flt is None:
        album_type_flt = ['single']
    if columns_excl is None:
        columns_excl = []

    tmp = df_stat.copy()

    tmp = tmp[
        (tmp.release_date > release_date_min)
        & (tmp.release_date < release_date_max)
        & (tmp.album.apply(lambda x: all(sub.lower() not in str(x).lower() for sub in album_flt)))
        & (tmp.album_artists.apply(lambda x: len(ast.literal_eval(x)) <= max_artists_in_album))
        & (~tmp.album_type.isin(album_type_flt))
    ]

    if other_album_author is False:
        tmp = tmp[tmp.apply(lambda x: x['artist'] in ast.literal_eval(x['album_artists']), axis=1)]

    columns_excl = [c for c in columns_excl if c in tmp.columns]

    return (
        tmp.drop_duplicates()
           .sort_values(by=['artist', 'release_date'], ascending=False)
           .drop(columns=columns_excl)
           .reset_index(drop=True)
    )
